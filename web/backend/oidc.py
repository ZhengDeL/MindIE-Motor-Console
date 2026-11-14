"""Optional standards-based OIDC code flow; Authlib + its joserfc verifier.

Only server-configured HTTPS issuer/endpoints are contacted. The control plane
keeps a short-lived, one-use server-side flow; provider tokens never reach the
browser or persistent account/audit data. This is an RP integration, not an IdP,
local MFA implementation, or proof of acceptance with any real enterprise IdP.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import ssl
import threading
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .auth import AuthManager, ROLES, _boolean, _digest
from .observability import register_secret


_ALGORITHMS = ("RS256", "ES256")


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return parsed.scheme + "://" + parsed.netloc


def _https(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.query or any(ord(c) < 33 for c in url)):
        raise ValueError("OIDC URLs must be explicitly configured HTTPS URLs without credentials/query/fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Invalid OIDC endpoint port") from exc


def _secret() -> str:
    value, filename = os.getenv("MOTOR_OIDC_CLIENT_SECRET", ""), os.getenv("MOTOR_OIDC_CLIENT_SECRET_FILE", "")
    if value and filename:
        raise ValueError("Configure only one OIDC client-secret source")
    if filename:
        try:
            if Path(filename).stat().st_size > 8192:
                raise ValueError("OIDC secret file is too large")
            value = Path(filename).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("The OIDC client-secret file is unavailable") from exc
    return value


@dataclass(frozen=True)
class OIDCSettings:
    enabled: bool = False
    issuer: str = ""
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    redirect_uri: str = ""
    token_auth_method: str = "client_secret_basic"
    role_claim: str = "groups"
    role_mapping: dict[str, str] = field(default_factory=dict)
    endpoint_origins: tuple[str, ...] = ()
    ca_bundle: str = ""
    flow_ttl: int = 300

    def __post_init__(self):
        if not self.enabled:
            return
        _https(self.issuer)
        _https(self.redirect_uri)
        if not self.redirect_uri.endswith("/api/auth/oidc/callback"):
            raise ValueError("OIDC redirect_uri must end with /api/auth/oidc/callback")
        if not self.client_id or len(self.client_id) > 512:
            raise ValueError("OIDC requires a client_id")
        if self.token_auth_method not in {"client_secret_basic", "client_secret_post", "none"}:
            raise ValueError("Unsupported OIDC token authentication method")
        if self.token_auth_method != "none" and not self.client_secret:
            raise ValueError("Confidential OIDC clients require a server-side secret")
        if self.token_auth_method == "none" and self.client_secret:
            raise ValueError("A public OIDC client must not configure a client secret")
        if not 60 <= self.flow_ttl <= 600:
            raise ValueError("OIDC flow lifetime must be 60..600 seconds")
        if not isinstance(self.role_mapping, dict) or any(not isinstance(k, str) or not k or v not in ROLES for k, v in self.role_mapping.items()):
            raise ValueError("OIDC role mapping must be a server-side object mapping exact claim values to console roles")
        if not self.role_claim or any(not item for item in self.role_claim.split(".")):
            raise ValueError("OIDC role claim must be a dotted claim path")
        for origin in self.endpoint_origins:
            _https(origin)
            if origin != _origin(origin):
                raise ValueError("OIDC endpoint allowlist entries must be exact HTTPS origins")

    @classmethod
    def from_env(cls):
        if not _boolean("MOTOR_OIDC_ENABLED", "false"):
            return cls()
        try:
            mapping = json.loads(os.getenv("MOTOR_OIDC_ROLE_MAPPING") or "{}")
        except ValueError as exc:
            raise ValueError("MOTOR_OIDC_ROLE_MAPPING must be a JSON object") from exc
        return cls(enabled=True, issuer=os.getenv("MOTOR_OIDC_ISSUER", ""),
                   client_id=os.getenv("MOTOR_OIDC_CLIENT_ID", ""), client_secret=_secret(),
                   redirect_uri=os.getenv("MOTOR_OIDC_REDIRECT_URI", ""),
                   token_auth_method=os.getenv("MOTOR_OIDC_TOKEN_AUTH_METHOD", "client_secret_basic"),
                   role_claim=os.getenv("MOTOR_OIDC_ROLE_CLAIM", "groups"), role_mapping=mapping,
                   endpoint_origins=tuple(v.strip().rstrip("/") for v in os.getenv("MOTOR_OIDC_ENDPOINT_ORIGINS", "").split(",") if v.strip()),
                   ca_bundle=os.getenv("MOTOR_OIDC_CA_BUNDLE", ""))


class OIDCManager:
    def __init__(self, auth: AuthManager, settings: OIDCSettings, *, transport=None):
        # Optional dependencies are imported only when the integration is enabled.
        try:
            import httpx2
            from authlib.integrations.httpx_client import OAuth2Client
            from authlib.oidc.core import CodeIDToken
            from joserfc import jwt
            from joserfc.jwk import KeySet
        except ImportError as exc:
            raise RuntimeError("OIDC requires Authlib>=1.9.2,<2, httpx2>=2.13.1,<3 and joserfc>=1.6.8,<2") from exc
        self.http, self.client_type, self.claim_type = httpx2, OAuth2Client, CodeIDToken
        self.jwt, self.key_set = jwt, KeySet
        self.auth, self.settings, self.transport = auth, settings, transport
        self.tls = ssl.create_default_context(cafile=settings.ca_bundle or None)
        self.guard, self.metadata_cache, self.keys_cache = threading.RLock(), None, None
        self.flow_cookie = "__Host-motor_console_oidc_flow" if auth.settings.secure_cookie else "motor_console_oidc_flow"
        register_secret(settings.client_secret)
        auth.sso_status = {"enabled": True, "login_url": "/api/auth/oidc/login"}

    def _kwargs(self):
        return {"verify": self.tls, "trust_env": False, "follow_redirects": False,
                "timeout": 10.0, "transport": self.transport}

    def _get_json(self, url):
        # Discovery/JWKS URLs come only from the configured issuer's verified
        # metadata and an explicit origin allowlist, never JWT jku/x5u headers.
        _https(url)
        if _origin(url) not in {_origin(self.settings.issuer), *self.settings.endpoint_origins}:
            raise ValueError("OIDC metadata endpoint origin is not allowed")
        with self.http.Client(**self._kwargs()) as client:
            with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                if response.status_code != 200:
                    raise ValueError("OIDC metadata request failed")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise ValueError("OIDC metadata exceeds size limit")
                    chunks.append(chunk)
                value = json.loads(b"".join(chunks))
        if not isinstance(value, dict):
            raise ValueError("OIDC metadata is not an object")
        return value

    def metadata(self):
        with self.guard:
            if self.metadata_cache and self.auth.clock() - self.metadata_cache[0] < 300:
                return self.metadata_cache[1]
            data = self._get_json(self.settings.issuer.rstrip("/") + "/.well-known/openid-configuration")
            if data.get("issuer") != self.settings.issuer:
                raise ValueError("OIDC discovery issuer mismatch")
            allowed = {_origin(self.settings.issuer), *self.settings.endpoint_origins}
            for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                _https(data.get(key, ""))
                if _origin(data[key]) not in allowed:
                    raise ValueError("OIDC discovered endpoint origin is not allowed")
            if "code" not in data.get("response_types_supported", []):
                raise ValueError("OIDC provider does not advertise authorization-code support")
            if "S256" not in data.get("code_challenge_methods_supported", ["S256"]):
                raise ValueError("OIDC provider does not support S256 PKCE")
            if self.settings.token_auth_method not in data.get("token_endpoint_auth_methods_supported", ["client_secret_basic"]):
                raise ValueError("OIDC client authentication method is not supported")
            self.metadata_cache = (self.auth.clock(), data)
            return data

    def _keys(self, *, refresh=False):
        with self.guard:
            if not refresh and self.keys_cache and self.auth.clock() - self.keys_cache[0] < 300:
                return self.keys_cache[1]
            jwks = self._get_json(self.metadata()["jwks_uri"])
            keys = jwks.get("keys")
            if not isinstance(keys, list) or not 1 <= len(keys) <= 64:
                raise ValueError("OIDC JWKS is unavailable or too large")
            self.keys_cache = (self.auth.clock(), jwks)
            return jwks

    def _oauth_client(self, **extra):
        return self.client_type(self.settings.client_id, self.settings.client_secret or None,
                                token_endpoint_auth_method=self.settings.token_auth_method,
                                redirect_uri=self.settings.redirect_uri, scope="openid profile",
                                code_challenge_method="S256", **self._kwargs(), **extra)

    def role(self, claims):
        value = claims
        for component in self.settings.role_claim.split("."):
            value = value.get(component) if isinstance(value, dict) else None
        values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
        roles = {self.settings.role_mapping[v] for v in values if isinstance(v, str) and v in self.settings.role_mapping}
        return "admin" if "admin" in roles else "operator" if "operator" in roles else "viewer"

    def start(self, request: Request):
        if self.auth.setup_required():
            raise HTTPException(409, "请先初始化本地管理员")
        # No open return_to parameter. Every attempt ends at this console's '/'.
        state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        self.auth.throttle(request, "oidc-start:" + state)
        metadata = self.metadata()
        with self._oauth_client(state=state) as client:
            url, _ = client.create_authorization_url(metadata["authorization_endpoint"], state=state,
                                                       code_verifier=verifier, nonce=nonce, response_mode="query")
        now = self.auth.clock()
        with self.auth.transaction(write=True) as db:
            db.oidc.remove_expired(now)
            db.oidc.create(_digest(state), _digest(browser), verifier, nonce, now, now + self.settings.flow_ttl)
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(self.flow_cookie, browser, max_age=self.settings.flow_ttl, path="/",
                            httponly=True, secure=self.auth.settings.secure_cookie, samesite="lax")
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    def _consume(self, request):
        params = request.query_params
        if any(len(params.getlist(key)) > 1 for key in ("code", "state", "iss", "error")):
            raise ValueError("OIDC callback parameters are ambiguous")
        state, browser = params.get("state", ""), request.cookies.get(self.flow_cookie, "")
        if not 20 <= len(state) <= 128 or not 20 <= len(browser) <= 128:
            raise ValueError("OIDC browser/state binding missing")
        with self.auth.transaction(write=True) as db:
            flow = db.oidc.get(_digest(state)).fetchone()
            if not flow or flow["consumed"] or self.auth.clock() >= flow["expires_at"] or not hmac_compare(flow["browser_hash"], _digest(browser)):
                raise ValueError("OIDC flow expired, consumed or belongs to another browser")
            db.oidc.consume(_digest(state))
        if params.get("error") or params.get("iss", self.settings.issuer) != self.settings.issuer:
            raise ValueError("OIDC provider rejected authorization or issuer mismatch")
        code = params.get("code", "")
        if not code or len(code) > 4096:
            raise ValueError("OIDC authorization code is missing or too large")
        return flow, code

    def _claims(self, encoded, flow, access_token):
        if not isinstance(encoded, str) or len(encoded) > 32768:
            raise ValueError("OIDC ID token is missing or too large")
        header = json.loads(base64.urlsafe_b64decode(encoded.split(".")[0] + "==="))
        if (not isinstance(header, dict) or header.get("alg") not in _ALGORITHMS
                or not isinstance(header.get("kid"), str) or not 1 <= len(header["kid"]) <= 128
                or any(k in header for k in ("jku", "jwk", "x5u"))):
            raise ValueError("OIDC signing header is unsupported")
        jwks = self._keys()
        if not any(k.get("kid") == header["kid"] for k in jwks["keys"]):
            jwks = self._keys(refresh=True)  # support a normal provider key rotation
        selected = [k for k in jwks["keys"] if k.get("kid") == header["kid"] and k.get("kty") in {"RSA", "EC"}
                    and k.get("use", "sig") == "sig" and "verify" in k.get("key_ops", ["verify"])]
        if len(selected) != 1:
            raise ValueError("OIDC signing key is unknown or ambiguous")
        token = self.jwt.decode(encoded, self.key_set.import_key_set({"keys": selected}), algorithms=_ALGORITHMS)
        claims = self.claim_type(token.claims, token.header,
                                 options={"iss": {"essential": True, "value": self.settings.issuer},
                                          "aud": {"essential": True, "value": self.settings.client_id}},
                                 params={"nonce": flow["nonce"], "client_id": self.settings.client_id, "access_token": access_token})
        claims.validate(now=self.auth.clock(), leeway=30)
        if not isinstance(claims.get("sub"), str) or not 1 <= len(claims["sub"]) <= 255:
            raise ValueError("OIDC subject is invalid")
        if claims["iat"] < flow["created_at"] - 60 or claims["iat"] > self.auth.clock() + 30:
            raise ValueError("OIDC ID token was not issued for this authorization attempt")
        return claims

    def callback(self, request):
        response = RedirectResponse("/", status_code=303)
        try:
            flow, code = self._consume(request)
            with self._oauth_client() as client:
                token = client.fetch_token(self.metadata()["token_endpoint"], grant_type="authorization_code", code=code,
                                           code_verifier=flow["verifier"], redirect_uri=self.settings.redirect_uri)
            claims = self._claims(token.get("id_token"), flow, token.get("access_token"))
            self.auth.federated_login(request, response, issuer=self.settings.issuer, subject=claims["sub"],
                                      display_name=str(claims.get("name") or claims.get("preferred_username") or "企业账号"), role=self.role(claims))
        except Exception:
            # Do not echo/log provider errors, authorization codes, ID/access
            # tokens or client secrets. A consumed attempt is never replayed.
            with self.auth.transaction(write=True) as db:
                self.auth.security_event(db, "anonymous", "oidc_login", "provider", "denied")
            response = RedirectResponse("/?auth_error=oidc", status_code=303)
        response.delete_cookie(self.flow_cookie, path="/", secure=self.auth.settings.secure_cookie, httponly=True, samesite="lax")
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response


def hmac_compare(one, two):
    import hmac
    return hmac.compare_digest(one.encode(), two.encode())


def create_oidc_router(auth: AuthManager, settings: OIDCSettings | None = None, *, transport=None) -> APIRouter:
    settings = settings or OIDCSettings.from_env()
    router = APIRouter(prefix="/api/auth/oidc", tags=["enterprise identity"])
    if not settings.enabled:
        return router
    manager = OIDCManager(auth, settings, transport=transport)
    router.oidc_manager = manager  # application integration/test introspection

    @router.get("/login")
    def login(request: Request):
        try:
            return manager.start(request)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "企业身份提供方暂不可用或配置无效")

    @router.get("/callback")
    def callback(request: Request):
        return manager.callback(request)

    return router
