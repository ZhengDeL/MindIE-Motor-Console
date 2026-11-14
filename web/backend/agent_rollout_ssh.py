"""Fingerprint-first SSH transport with scoped secrets and bounded fixed RPCs."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
from pathlib import Path
import shlex
import socket
import time

from .agent_rollout_models import SSHConnection, SSHCredentials


REMOTE_CODES = frozenset("""
agent_credential_invalid agent_port_invalid agent_preparation_failed agent_tls_environment_missing
agent_tls_preparation_failed artifact_changed_during_read artifact_checksum_mismatch artifact_identity_invalid
artifact_size_mismatch artifact_stream_incomplete existing_agent_refused helper_cleanup_unverified
helper_inventory_limit host_installation_busy host_reservation_ownership_conflict image_architecture_mismatch
image_reference_invalid install_path_overlaps_docker_storage installation_name_invalid installation_ownership_conflict
installer_action_invalid installer_materials_invalid installer_request_limit kubeconfig_private_file_required
kubeconfig_unavailable loaded_image_identity_mismatch loaded_image_invalid operation_identity_invalid
remote_architecture_unsupported remote_command_failed remote_command_output_limit remote_command_timeout
remote_command_unavailable remote_compose_v2_required remote_container_inventory_limit remote_identity_changed remote_install_root_invalid
remote_installation_conflict remote_path_invalid remote_path_permissions_invalid remote_private_file_invalid
remote_root_linux_python_required remote_symlink_refused runtime_invalid started_agent_identity_unverified
remote_installer_failed
""".split())


class RolloutError(RuntimeError):
    def __init__(self, code, *, stage=""):
        self.code, self.stage = code, stage
        super().__init__(code)


class SSHMaterial:
    """Mutable references can be dropped; Python strings cannot be reliably zeroed."""
    def __init__(self, value: SSHCredentials):
        self.values = {"method": value.method, "password": value.password.get_secret_value(),
                       "private_key": value.private_key.get_secret_value(), "passphrase": value.passphrase.get_secret_value()}

    def clear(self):
        self.values.clear()


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def discover_host_key(host: str, port: int):
    import paramiko
    transport, connection = None, None
    try:
        connection = socket.create_connection((host, port), timeout=10)
        transport = paramiko.Transport(connection)
        transport.set_log_channel("motor_console.rollout.ssh")
        transport.set_hexdump(False)
        transport.start_client(timeout=10)
        key = transport.get_remote_server_key()
        return {"host": host, "port": port, "key_type": key.get_name(),
                "fingerprint": fingerprint(key), "verified": False}
    except Exception:
        raise RolloutError("ssh_host_key_discovery_failed") from None
    finally:
        if transport is not None:
            transport.close()
        elif connection is not None:
            connection.close()


class SSHSession:
    def __init__(self, connection: SSHConnection, material: SSHMaterial):
        self.connection, self.material, self.client = connection, material, None
        logger = logging.getLogger("motor_console.rollout.ssh")
        logger.handlers = [logging.NullHandler()]; logger.propagate = False
        logger.setLevel(logging.CRITICAL)

    def __enter__(self):
        import paramiko
        expected = self.connection.host_key_sha256

        class FingerprintPolicy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                if not hmac.compare_digest(fingerprint(key), expected):
                    raise RolloutError("ssh_host_key_mismatch")

        client = paramiko.SSHClient()
        client.set_log_channel("motor_console.rollout.ssh")
        client.set_missing_host_key_policy(FingerprintPolicy())
        values = self.material.values
        options = {"hostname": self.connection.host, "port": self.connection.port, "username": self.connection.username,
                   "look_for_keys": False, "allow_agent": False, "timeout": 10, "banner_timeout": 10,
                   "auth_timeout": 15, "channel_timeout": 15}
        try:
            if values["method"] == "password":
                options["password"] = values["password"]
            else:
                key = None
                for kind in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
                    try:
                        key = kind.from_private_key(io.StringIO(values["private_key"]), password=values["passphrase"] or None)
                        break
                    except (paramiko.SSHException, ValueError):
                        continue
                if key is None:
                    raise RolloutError("ssh_private_key_invalid")
                options["pkey"] = key
            client.connect(**options)
            transport = client.get_transport()
            if transport is None or not transport.is_authenticated():
                raise RolloutError("ssh_authentication_failed")
            transport.set_hexdump(False)
            if not hmac.compare_digest(fingerprint(transport.get_remote_server_key()), expected):
                raise RolloutError("ssh_host_key_mismatch")
            self.client = client
            return self
        except RolloutError:
            client.close(); raise
        except paramiko.AuthenticationException:
            client.close(); raise RolloutError("ssh_authentication_failed") from None
        except Exception:
            client.close(); raise RolloutError("ssh_connection_failed") from None
        finally:
            options.clear()

    def __exit__(self, *_):
        if self.client is not None:
            self.client.close(); self.client = None

    def rpc(self, program: str, payload: dict, *, timeout=90, stream=None, progress=None):
        if self.client is None:
            raise RolloutError("ssh_connection_closed")
        transport = self.client.get_transport()
        channel = transport.open_session(timeout=15)
        channel.settimeout(15)
        command = ("sudo -n -- " if self.connection.privilege == "sudo_n" else "") + "python3 -c " + shlex.quote(program)
        header = (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        if len(header) > 256 * 1024:
            channel.close(); raise RolloutError("installer_request_limit")
        try:
            deadline = time.monotonic() + timeout
            channel.exec_command(command)
            channel.sendall(header)
            if stream is not None:
                total = 0; expected = payload["artifact"]["size_bytes"]
                while chunk := stream.read(1024 * 1024):
                    if time.monotonic() > deadline:
                        raise RolloutError("installer_stage_timeout")
                    total += len(chunk)
                    if total > expected:
                        raise RolloutError("artifact_size_mismatch")
                    channel.sendall(chunk)
                    if progress: progress(total, expected)
                if total != expected:
                    raise RolloutError("artifact_stream_incomplete")
            channel.shutdown_write()
            output, errors = bytearray(), 0
            while True:
                while channel.recv_ready():
                    output.extend(channel.recv(65536))
                    if len(output) > 65536:
                        raise RolloutError("installer_response_limit")
                while channel.recv_stderr_ready():
                    errors += len(channel.recv_stderr(65536))
                    if errors > 65536:
                        raise RolloutError("installer_response_limit")
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    code = channel.recv_exit_status(); break
                if time.monotonic() > deadline:
                    raise RolloutError("installer_stage_timeout")
                time.sleep(.05)
            try:
                response = json.loads(output)
            except (ValueError, UnicodeError, RecursionError):
                raise RolloutError("installer_response_invalid") from None
            if not isinstance(response, dict) or response.get("ok") is not True or code:
                error = response.get("error_code", "remote_installer_failed") if isinstance(response, dict) else "remote_installer_failed"
                # A compromised/old remote cannot supply arbitrary error text.
                if not isinstance(error, str) or error not in REMOTE_CODES:
                    error = "remote_installer_failed"
                raise RolloutError(error)
            result = response.get("result")
            if not isinstance(result, dict):
                raise RolloutError("installer_response_invalid")
            return result
        except RolloutError:
            raise
        except Exception:
            raise RolloutError("ssh_stage_connection_lost") from None
        finally:
            channel.close()

    def upload_artifact(self, program, payload, stream, *, progress=None):
        """Root uses exclusive SFTP; sudo streams to the fixed root-owned writer."""
        if self.connection.privilege == "sudo_n":
            return self.rpc(program, {**payload, "action": "upload"}, stream=stream, progress=progress, timeout=600)
        expected = payload["artifact"]["size_bytes"]
        # Both path components were validated by the local public model. Never
        # follow an arbitrary pathname returned by the remote side.
        target = payload["install_root"] + "/" + payload["spec"]["installation_name"] + "/agent-image.tar.gz"
        sftp, destination = None, None
        try:
            sftp = self.client.open_sftp(); sftp.get_channel().settimeout(15)
            destination = sftp.open(target, "wx"); sftp.chmod(target, 0o600)
            total, digest, deadline = 0, hashlib.sha256(), time.monotonic() + 600
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > expected or time.monotonic() > deadline:
                    raise RolloutError("artifact_upload_limit")
                destination.write(chunk); digest.update(chunk)
                if progress: progress(total, expected)
            destination.flush()
            if total != expected or digest.hexdigest() != payload["artifact"]["sha256"]:
                raise RolloutError("artifact_checksum_mismatch")
            return {"uploaded_bytes": total, "sha256": digest.hexdigest()}
        except RolloutError:
            raise
        except Exception:
            raise RolloutError("artifact_upload_failed") from None
        finally:
            if destination is not None: destination.close()
            if sftp is not None: sftp.close()
