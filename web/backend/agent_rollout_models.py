"""Strict public inputs for the administrator-only Agent installer."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator, field_validator

from .agent_security import validate_agent_url


INSTALL_NAME = r"^[a-z0-9][a-z0-9-]{0,62}$"
IMAGE_REFERENCE = r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}$"
ARTIFACT_ID = r"^[a-z0-9][a-z0-9._-]{0,63}$"


def validate_install_root(value: str) -> str:
    """Keep administrator-selected installation roots inside this product's namespace."""
    if (not isinstance(value, str) or not value.startswith("/") or "//" in value
            or any(part in {".", ".."} for part in value.split("/"))
            or not re.fullmatch(r"/[A-Za-z0-9_./-]{1,400}", value)):
        raise ValueError("MOTOR_AGENT_ROLLOUT_ROOT must be an independent absolute installation directory")
    parts = value.split("/")
    if any(part.lower() in {"hx", "shhc"} or part.lower().startswith("hx-") or part.lower().endswith("-hx") for part in parts):
        raise ValueError("Agent installation cannot reuse a directory reserved for another product")
    if value == "/opt/motor-console/agents":
        return value
    if (not re.fullmatch(r"/(?:var/lib|opt|srv|mnt/models)/[A-Za-z0-9_./-]{1,300}", value)
            or not any(part in {"motor-console", "qinghuan"} or part.startswith(("motor-console-", "qinghuan-")) or part.endswith("-qh") for part in parts)):
        raise ValueError("MOTOR_AGENT_ROLLOUT_ROOT requires a dedicated motor-console, qinghuan or -qh installation directory")
    return value


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HostKeyInput(StrictInput):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def literal_host(cls, value):
        try:
            address = ipaddress.ip_address(value)
            if address.is_unspecified or address.is_multicast or address.is_link_local:
                raise ValueError("SSH address must be a reachable management address")
            return str(address)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value):
                raise ValueError("SSH host must be an IP address or literal hostname") from None
            # Numeric pseudo-hosts are not interpreted by resolver shortcuts.
            if re.fullmatch(r"[0-9.]+", value):
                raise ValueError("SSH address is invalid")
            return value.lower()


class SSHConnection(HostKeyInput):
    username: str = Field(default="root", pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}\$?$")
    host_key_sha256: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")
    privilege: Literal["root", "sudo_n"] = "root"


class SSHCredentials(StrictInput):
    method: Literal["password", "private_key"] = "password"
    password: SecretStr = Field(default_factory=lambda: SecretStr(""), max_length=4096)
    private_key: SecretStr = Field(default_factory=lambda: SecretStr(""), max_length=65536)
    passphrase: SecretStr = Field(default_factory=lambda: SecretStr(""), max_length=4096)

    @model_validator(mode="after")
    def matching_material(self):
        password, key = self.password.get_secret_value(), self.private_key.get_secret_value()
        if self.method == "password" and (not password or key or self.passphrase.get_secret_value()):
            raise ValueError("Password authentication needs only a non-empty password")
        if self.method == "private_key" and (not key or password or "PRIVATE KEY-----" not in key):
            raise ValueError("Key authentication needs an inline private key and optional passphrase")
        return self


class RolloutSpec(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    address: str
    runtime: Literal["docker", "kubernetes"]
    agent_url: str = Field(min_length=1, max_length=2048)
    agent_port: int = Field(default=19082, ge=1024, le=65535)
    nic_name: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_.:-]*$")
    labels: list[str] = Field(default_factory=list, max_length=32)
    installation_name: str = Field(pattern=INSTALL_NAME)
    tls_mode: Literal["managed_tls", "existing_https"] = "managed_tls"
    https_proxy_confirmed: bool = False
    image_source: Literal["registry", "artifact"] = "registry"
    image: str = Field(default="", max_length=256)
    artifact_id: str = Field(default="", max_length=64)
    kubeconfig_path: str = Field(default="", max_length=512)
    cpus: str = Field(default="1", max_length=12, pattern=r"^[0-9]+(?:\.[0-9]{1,3})?$")
    memory: str = Field(default="1g", max_length=12, pattern=r"^[1-9][0-9]*[kmg]$")
    pids: int = Field(default=256, ge=64, le=4096)
    native_concurrency: int = Field(default=1, ge=1, le=4)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Agent name cannot be blank or contain control characters")
        return value.strip()

    @field_validator("address")
    @classmethod
    def management_ipv4(cls, value):
        address = ipaddress.IPv4Address(value)
        if address.is_unspecified or address.is_link_local or address.is_multicast or address.is_loopback:
            raise ValueError("Node address must be its management IPv4 address")
        return str(address)

    @field_validator("agent_url")
    @classmethod
    def https_origin(cls, value):
        value = validate_agent_url(value)
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.path:
            raise ValueError("Agent URL must be an HTTPS origin")
        return value

    @field_validator("labels")
    @classmethod
    def bounded_labels(cls, value):
        if any(not item or len(item) > 64 or any(ord(c) < 32 for c in item) for item in value):
            raise ValueError("Node labels must be non-empty text of at most 64 characters")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def deployment_contract(self):
        parsed = urlsplit(self.agent_url)
        if self.tls_mode == "managed_tls" and (parsed.hostname != self.address or (parsed.port or 443) != self.agent_port):
            raise ValueError("Managed TLS must use the node management IP and selected Agent port")
        if self.tls_mode == "existing_https" and not self.https_proxy_confirmed:
            raise ValueError("Confirm the existing HTTPS entry point before using it")
        if self.image_source == "registry":
            if not re.fullmatch(IMAGE_REFERENCE, self.image) or self.artifact_id:
                raise ValueError("Registry installation needs one digest-pinned image reference")
        elif not re.fullmatch(ARTIFACT_ID, self.artifact_id) or self.image:
            raise ValueError("Offline installation needs one staged artifact ID")
        if self.runtime == "kubernetes":
            parts = self.kubeconfig_path.split("/")
            if (not self.kubeconfig_path.startswith("/") or ".." in parts or "//" in self.kubeconfig_path
                    or any(c in self.kubeconfig_path for c in "\x00\r\n'\"$`")):
                raise ValueError("Kubernetes installation needs an absolute remote kubeconfig path")
        elif self.kubeconfig_path:
            raise ValueError("Docker installation does not mount a Kubernetes credential")
        from decimal import Decimal
        if not Decimal("0.25") <= Decimal(self.cpus) <= 32:
            raise ValueError("Agent CPU limit must be between 0.25 and 32")
        amount = int(self.memory[:-1]) * {"k": 1024, "m": 1024**2, "g": 1024**3}[self.memory[-1]]
        if not 512 * 1024**2 <= amount <= 64 * 1024**3:
            raise ValueError("Agent memory must be between 512m and 64g")
        return self


class PlanInput(StrictInput):
    spec: RolloutSpec
    connection: SSHConnection
    credentials: SSHCredentials


class ExecuteInput(StrictInput):
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_name: str = Field(min_length=1, max_length=100)
    credentials: SSHCredentials


class RecoverInput(StrictInput):
    credentials: SSHCredentials
