"""Bounded, durable node-local image operations; no force removal or global prune."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import queue
import re
import socket
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .docker_agent_state import intent_hash, operation_identifier
from .execution_policy import require_mutations
from .image_contract import validate_image_reference
from .motor_adapter import safe_name
from .network_budget import run_network
from .observability import register_secret

_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_QUEUE_LIMIT = 16


class RegistryAuthentication(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr

    @field_validator("username")
    @classmethod
    def username_literal(cls, value):
        if any(not 33 <= ord(character) <= 126 for character in value):
            raise ValueError("Registry username must be printable ASCII without whitespace")
        return value

    @field_validator("password")
    @classmethod
    def password_bounded(cls, value):
        if not 1 <= len(value.get_secret_value()) <= 4096:
            raise ValueError("Registry password must contain 1..4096 characters")
        return value


class _ImageOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(min_length=1, max_length=128)
    node_id: str = Field(min_length=1, max_length=96)

    @field_validator("operation_id")
    @classmethod
    def operation_literal(cls, value):
        return operation_identifier(value)

    @field_validator("node_id")
    @classmethod
    def node_literal(cls, value):
        return safe_name(value, "node_id")


class ImagePullInput(_ImageOperation):
    image: str = Field(min_length=1, max_length=256)
    registry_auth: RegistryAuthentication | None = None

    @field_validator("image")
    @classmethod
    def remote_reference(cls, value):
        value = validate_image_reference(value)
        if _IMAGE_ID.fullmatch(value):
            raise ValueError("Pull requires a registry image reference, not a local image ID")
        return value


class ImageRemoveInput(_ImageOperation):
    image_id: str

    @field_validator("image_id")
    @classmethod
    def immutable_id(cls, value):
        if not _IMAGE_ID.fullmatch(value):
            raise ValueError("Removal requires a complete immutable sha256 image ID")
        return value


class _Interrupted(RuntimeError):
    pass


def _registry(reference: str) -> str:
    first = reference.split("/", 1)[0]
    return first.lower() if "/" in reference and ("." in first or ":" in first or first == "localhost") else "docker.io"


class ImageOperations:
    def __init__(self, runtime):
        self.runtime = runtime
        self.pending = queue.Queue(maxsize=_QUEUE_LIMIT)
        self.guard = threading.Lock()
        self.closed = threading.Event()
        self.failed = threading.Event()
        budget = os.getenv("MOTOR_IMAGE_TASK_SECONDS", "1800")
        if not budget.isascii() or not budget.isdecimal() or not 30 <= int(budget) <= 7200:
            raise ValueError("MOTOR_IMAGE_TASK_SECONDS must be between 30 and 7200")
        self.budget = int(budget)
        self.worker = threading.Thread(target=self._serve, name="motor-image-operation", daemon=True)
        self.worker.start()

    def receipt(self, identifier: str) -> dict | None:
        result = self.runtime.operation(identifier)
        if result and result.get("action") in {"image_pull", "image_remove"} and self.failed.is_set() \
                and result.get("operation_status") in {"accepted", "running"}:
            result = self.runtime.resource_ledger().interrupt(identifier, message="Image worker is unavailable; inspect the durable operation before retrying",
                                                      resources_may_exist=result["operation_status"] == "running")
        return result if result and result.get("action") in {"image_pull", "image_remove"} else None

    def submit(self, action: str, request: ImagePullInput | ImageRemoveInput) -> dict:
        require_mutations(self.runtime.read_only)
        if action not in {"image_pull", "image_remove"}:
            raise ValueError("Unsupported image operation")
        if isinstance(request, ImagePullInput) and request.registry_auth:
            register_secret(request.registry_auth.password.get_secret_value())
        identity = {"node_id": request.node_id}
        identity.update(image=request.image) if action == "image_pull" else identity.update(image_id=request.image_id)
        fingerprint = intent_hash(action, identity)
        state = self.runtime.resource_ledger()
        with self.guard:
            replay = state.replay(request.operation_id, fingerprint)
            if replay is not None:
                return replay
            if self.closed.is_set() or self.failed.is_set() or not self.worker.is_alive():
                raise HTTPException(503, "Image operation worker is unavailable; no operation was queued")
            if self.pending.full():
                raise HTTPException(429, "Image operation queue is full; no operation was accepted", headers={"Retry-After": "10"})
            if action == "image_remove":
                self._assert_unused(self.runtime.engine_client(), request.image_id)
            public_intent = {"image_reference": request.image} if action == "image_pull" else {"image_id": request.image_id}
            replay = state.begin(request.operation_id, fingerprint, action, "", request.node_id,
                                 public_intent=public_intent)
            if replay is not None:
                return replay
            self.pending.put_nowait((action, request, time.monotonic() + self.budget))
        return state.receipt(request.operation_id)

    def close(self):
        with self.guard:
            self.closed.set()
            while True:
                try:
                    task = self.pending.get_nowait()
                except queue.Empty:
                    break
                if task is not None:
                    _action, request, _deadline = task
                    try:
                        self.runtime.resource_ledger().finish(request.operation_id, error=ValueError("Agent shut down before this image operation executed"), resources_may_exist=False)
                    finally:
                        self.pending.task_done()
                else:
                    self.pending.task_done()
            self.pending.put_nowait(None)

    def _serve(self):
        while True:
            task = self.pending.get()
            try:
                if task is None:
                    return
                self._execute(*task)
            except Exception:
                # A failed journal cannot authorize another operation or erase
                # pending identities. Startup recovery preserves them unknown.
                self.failed.set()
                return
            finally:
                self.pending.task_done()

    @staticmethod
    def _check_time(deadline):
        if time.monotonic() >= deadline:
            raise _Interrupted("Image operation exceeded its total execution/queue budget; inspect the persistent receipt before retrying")

    def _auth(self, client, request, deadline):
        if request.registry_auth is None:
            return None
        registry = _registry(request.image)
        info = client.info().get("RegistryConfig")
        if not isinstance(info, dict):
            raise ValueError("Docker registry TLS policy could not be verified; credentials were not submitted")
        index = (info.get("IndexConfigs") or {}).get(registry)
        if index is not None and (not isinstance(index, dict) or type(index.get("Secure")) is not bool):
            raise ValueError("Docker registry security metadata is invalid; credentials were not submitted")
        if isinstance(index, dict) and index.get("Secure") is False:
            raise ValueError("Credentials cannot be sent to an insecure Docker registry")
        if not (isinstance(index, dict) and index.get("Secure") is True):
            host = urlsplit("//" + registry).hostname
            if not host:
                raise ValueError("Registry hostname is invalid")
            try:
                addresses = [ipaddress.ip_address(host)]
            except ValueError:
                async def resolve():
                    rows = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
                    return list({ipaddress.ip_address(row[4][0]) for row in rows})
                addresses = run_network(resolve, min(deadline, time.monotonic() + 5))
            networks = [ipaddress.ip_network(value) for value in info.get("InsecureRegistryCIDRs", [])]
            if not addresses or any(address.version == network.version and address in network for address in addresses for network in networks):
                raise ValueError("Registry resolves to an insecure Docker range; credentials were not submitted")
        return {"username": request.registry_auth.username,
                "password": request.registry_auth.password.get_secret_value(), "serveraddress": registry}

    @staticmethod
    def _image_result(image, reference):
        identifier = image.id
        if not isinstance(identifier, str) or not _IMAGE_ID.fullmatch(identifier):
            raise _Interrupted("Docker returned an unverifiable image identity")
        digests = image.attrs.get("RepoDigests") or []
        if not isinstance(digests, list) or len(digests) > 64 or any(not isinstance(value, str) or len(value) > 512 for value in digests):
            raise _Interrupted("Docker image digest metadata is invalid")
        if "@sha256:" in reference and not any(value.endswith("@" + reference.rsplit("@", 1)[1]) for value in digests):
            raise _Interrupted("The pulled digest cannot be bound to the requested reference")
        size = image.attrs.get("Size")
        if size is not None and (type(size) is not int or size < 0):
            raise _Interrupted("Docker image size metadata is invalid")
        return {"status": "ready", "image_id": identifier, "image_reference": reference,
                "repo_digests": digests, "size_bytes": size,
                "message": "Image is present in this Docker daemon; model compatibility and inference are separate checks"}

    @staticmethod
    def _assert_unused(client, identifier):
        items = client.api.containers(all=True)
        if not isinstance(items, list) or len(items) > 4096:
            raise HTTPException(409, "Container reference inventory exceeds its safe bounds; image removal refused")
        for item in items:
            image_id = item.get("ImageID")
            if not image_id and isinstance(item.get("Id"), str):
                image_id = client.containers.get(item["Id"]).attrs.get("Image")
            if not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id):
                raise HTTPException(409, "A container image reference could not be verified; image removal refused")
            if image_id == identifier:
                raise HTTPException(409, "Image is referenced by an existing container, including stopped containers; removal refused")

    def _execute(self, action, request, deadline):
        state = self.runtime.resource_ledger()
        write_attempted = False
        stream = None
        try:
            require_mutations(self.runtime.read_only)
            self._check_time(deadline)
            if self.closed.is_set():
                raise ValueError("Agent shut down before the image operation executed")
            state.running(request.operation_id)
            client = self.runtime.engine_client()
            if action == "image_pull":
                auth = self._auth(client, request, deadline)
                self._check_time(deadline)
                write_attempted = True
                stream = client.api.pull(request.image, stream=True, decode=True, auth_config=auth)
                for event in stream:
                    self._check_time(deadline)
                    if self.closed.is_set():
                        raise _Interrupted("Agent stopped during image pull; daemon work may still exist")
                    if not isinstance(event, dict):
                        raise _Interrupted("Docker returned an invalid image-pull event")
                    if event.get("error") or event.get("errorDetail"):
                        # Registry/daemon text can contain credentials. Return a
                        # stable diagnosis code rather than echoing raw errors.
                        message = str(event.get("error") or event.get("errorDetail"))[:4096].lower()
                        code = "registry_authentication_failed" if any(value in message for value in ("unauthorized", "denied", "authentication")) else "registry_pull_failed"
                        raise ValueError(code)
                result = self._image_result(client.images.get(request.image), request.image)
            else:
                self._assert_unused(client, request.image_id)
                self._check_time(deadline)
                write_attempted = True
                try:
                    client.images.remove(image=request.image_id, force=False, noprune=True)
                except Exception as error:
                    if getattr(error, "status_code", None) != 404:
                        raise
                try:
                    client.images.get(request.image_id)
                except Exception as error:
                    if getattr(error, "status_code", None) != 404:
                        raise
                else:
                    raise _Interrupted("Docker still reports the selected image ID; removal has not converged")
                result = {"status": "removed", "image_id": request.image_id,
                          "message": "The selected image ID was removed or already absent; no force or prune was used"}
            self._check_time(deadline)
            state.finish(request.operation_id, result=result, resources_may_exist=False)
        except _Interrupted as error:
            state.interrupt(request.operation_id, message=str(error), resources_may_exist=write_attempted)
        except Exception as error:
            # Keep registry secrets out of persistence and diagnostics even if a
            # third-party exception embeds the submitted authentication payload.
            status_code = getattr(error, "status_code", None)
            known = isinstance(error, (ValueError, HTTPException)) or type(status_code) is int and 400 <= status_code < 500
            message = str(error.detail) if isinstance(error, HTTPException) else str(error)
            if not known or write_attempted:
                message = "Image operation failed (" + type(error).__name__ + "); inspect registry access and the persistent receipt before retrying"
            if isinstance(error, ValueError) and str(error) in {"registry_authentication_failed", "registry_pull_failed"}:
                message = str(error)
            if write_attempted and not known:
                state.interrupt(request.operation_id, message=message, resources_may_exist=True)
            else:
                state.finish(request.operation_id, error=ValueError(message), resources_may_exist=write_attempted)
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
