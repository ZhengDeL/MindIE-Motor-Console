"""Mandatory auth dispatch, independent of the selected HTTP implementation."""
from __future__ import annotations

from fastapi import HTTPException

# Public entry points are exact method/path pairs. An auth namespace is never
# an exemption: third-party account and self-service modules may omit Depends.
PUBLIC_AUTH_ROUTES = frozenset({
    ("GET", "/api/auth/status"), ("HEAD", "/api/auth/status"),
    ("POST", "/api/auth/bootstrap"), ("POST", "/api/auth/login"),
    ("GET", "/api/auth/oidc/login"), ("GET", "/api/auth/oidc/callback"),
})


def auth_path(path: str) -> bool:
    path = path.rstrip("/")
    return path == "/api/auth" or path.startswith("/api/auth/")


def account_path(path: str) -> bool:
    path = path.rstrip("/")
    return path == "/api/auth/users" or path.startswith("/api/auth/users/")


def public_auth_request(path: str, method: str) -> bool:
    return (method, path.rstrip("/")) in PUBLIC_AUTH_ROUTES


def authenticate_session(session, request, *, required=True):
    """Share one verified Session result only within this exact HTTP request.

    The provider instance is part of the marker; a different selected Session
    can never reuse it. No process/global cache is used. Revocation is checked
    again on the next request, including after logout or account updates.
    """
    cached = getattr(request.state, "_motor_verified_session", None)
    if not (isinstance(cached, tuple) and len(cached) == 2 and cached[0] is session):
        principal = session.authenticate(request, required=required)
        if principal is not None and (
                not isinstance(getattr(principal, "user_id", None), str) or not principal.user_id
                or getattr(principal, "role", None) not in {"admin", "operator", "viewer"}
                or getattr(principal, "kind", None) not in {"session", "machine"}):
            raise HTTPException(503, {"code": "invalid_identity_result", "message": "所选会话提供者返回了不兼容身份，已拒绝请求"})
        request.state._motor_verified_session = (session, principal)
    else:
        principal = cached[1]
    if principal is None and required:
        raise HTTPException(401, "请先登录控制台")
    return principal
