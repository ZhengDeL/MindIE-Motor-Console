"""Standalone stdlib-only, read-only Ascend host sampler for a systemd timer.

Only the two fixed `npu-smi info` / `npu-smi info -m` commands are executed.
Supported table layouts are positively validated; unknown output fails closed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

MAX_OUTPUT_BYTES = 1024 * 1024
MAX_DEVICES = 256
MAX_PROCESSES = 128
MIB = 1024 * 1024
_BORDER = re.compile(r"\+[+\-=]+\+")
_TOP = re.compile(r"\|\s*(\d+)\s+([A-Za-z0-9_.-]+)\s*\|\s*([A-Za-z]+|-)\s*\|\s*([\d.]+|-|NA)\s+([\d.]+|-|NA)\s+(?:\d+\s*/\s*\d+|-)\s*\|")
_BOTTOM = re.compile(r"\|\s*(\d+)\s+(\d+)\s*\|\s*([0-9A-Fa-f]{4}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7])\s*\|\s*([\d.]+|-|NA)\s+(\d+\s*/\s*\d+|-|NA)\s+(\d+\s*/\s*\d+|-|NA)\s*\|")
_LEGACY_HEADER = "NPU ID Chip ID Chip Logic ID Chip Name"
_LEGACY_PROFILE = "ascend910b3-25.5.2-single-chip"
_LEGACY_BOTTOM = re.compile(r"\|\s*(\d+)\s*\|\s*([0-9A-Fa-f]{4}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7])\s*\|\s*([\d.]+|-|NA)\s+(\d+\s*/\s*\d+|-|NA)\s+(\d+\s*/\s*\d+|-|NA)\s*\|")


class ObservationError(ValueError):
    pass


def _integer(raw: str, maximum=4095) -> int:
    if not raw.isascii() or not raw.isdecimal() or not 0 <= int(raw) <= maximum:
        raise ObservationError("invalid integer in host observation")
    return int(raw)


def _number(raw: str, low=0, high=100):
    if raw in {"-", "NA"}:
        return None
    try:
        value = float(raw)
    except ValueError as error:
        raise ObservationError("invalid numeric host observation") from error
    if not math.isfinite(value) or not low <= value <= high:
        raise ObservationError("host observation is outside supported bounds")
    return value


def host_identity(host_root: str | Path = "/") -> str:
    for relative in ("etc/machine-id", "var/lib/dbus/machine-id"):
        path = Path(host_root) / relative
        try:
            if path.is_symlink():
                continue
            with path.open("rb") as source:
                raw = source.read(257).decode("ascii").strip()
            if re.fullmatch(r"[a-fA-F0-9]{32}", raw):
                return "sha256:" + hashlib.sha256(raw.lower().encode()).hexdigest()
        except (OSError, UnicodeError):
            continue
    raise ObservationError("a stable host machine identity is required")


def boot_clock(host_root: str | Path = "/") -> dict:
    """Bounded Linux boot-time evidence, independent of NTP and this process."""
    def read(relative):
        path = Path(host_root) / relative
        if path.is_symlink():
            raise ObservationError("host clock symlinks are not accepted")
        with path.open("rb") as source:
            value = source.read(513)
        if len(value) > 512:
            raise ObservationError("host clock exceeds its read limit")
        return value.decode("ascii").strip()
    boot = read("proc/sys/kernel/random/boot_id")
    fields = read("proc/uptime").split()
    uptime = fields[0] if fields else ""
    if (not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot)
            or not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,3})?", uptime)
            or read("proc/sys/kernel/random/boot_id") != boot):
        raise ObservationError("host boot clock could not be verified")
    return {"schema": 1, "boot_id": boot, "uptime_ms": int(float(uptime) * 1000)}


def _mapping(text: str) -> dict:
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    if not rows or " ".join(rows[0].split()) != "NPU ID Chip ID Chip Logic ID Chip Phy-ID Chip Name":
        raise ObservationError("unsupported npu-smi mapping header")
    result, logical_ids, physical_ids = {}, set(), set()
    for row in rows[1:]:
        parts = row.split()
        if len(parts) != 5:
            raise ObservationError("unsupported npu-smi mapping row")
        npu, chip = _integer(parts[0]), _integer(parts[1])
        if parts[2:] == ["-", "-", "Mcu"]:
            continue
        logic, physical = _integer(parts[2]), _integer(parts[3])
        if not re.fullmatch(r"Ascend[A-Za-z0-9_.-]+", parts[4]):
            raise ObservationError("unsupported npu-smi mapping model")
        pair = (npu, chip)
        if pair in result or logic in logical_ids or physical in physical_ids or len(result) >= MAX_DEVICES:
            raise ObservationError("ambiguous or oversized device mapping")
        result[pair] = {"logical_device_id": logic, "physical_device_id": physical, "model_name": parts[4]}
        logical_ids.add(logic)
        physical_ids.add(physical)
    if not result:
        raise ObservationError("no physical NPU mapping was observed")
    return result


def _legacy_mapping(text: str) -> dict:
    """The documented A2 single-chip NPU ID is physical, never inferred from logic."""
    result, logical_ids, mcus = {}, set(), set()
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    if not rows or " ".join(rows[0].split()) != _LEGACY_HEADER:
        raise ObservationError("unsupported legacy npu-smi mapping header")
    for row in rows[1:]:
        parts = row.split()
        if len(parts) == 4 and parts[1:] == ["1", "-", "Mcu"]:
            npu = _integer(parts[0])
            if npu in mcus:
                raise ObservationError("duplicate legacy MCU mapping")
            mcus.add(npu)
            continue
        if len(parts) != 5 or parts[1] != "0" or parts[3:] != ["Ascend", "910B3"]:
            raise ObservationError("unsupported legacy single-chip mapping row")
        npu, logic = _integer(parts[0]), _integer(parts[2])
        if (npu, 0) in result or logic in logical_ids or len(result) >= 16:
            raise ObservationError("ambiguous or oversized legacy device mapping")
        result[npu, 0] = {"logical_device_id": logic, "physical_device_id": npu, "model_name": "910B3"}
        logical_ids.add(logic)
    if not result or mcus != {npu for npu, _ in result}:
        raise ObservationError("legacy mapping does not cover unique compute/MCU pairs")
    return result


def _legacy_node_proof(mapping: dict, host_root) -> dict:
    """Read only kernel identity metadata; never open a davinci device or query it."""
    root = Path(host_root)
    if not root.is_absolute() or root.resolve() != root:
        raise ObservationError("legacy host root is not a real absolute directory")
    before = boot_clock(root)
    sys_devices = (root / "sys/devices").resolve(strict=True)
    proof, identities = [], set()
    def read(path):
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
        with os.fdopen(descriptor, "rb") as source:
            details = os.fstat(source.fileno())
            if not stat.S_ISREG(details.st_mode) or details.st_uid != 0 or details.st_mode & 0o022:
                raise ObservationError("legacy kernel identity file is untrusted")
            value = source.read(4096)
        if len(value) == 4096:
            raise ObservationError("legacy kernel identity exceeds its read limit")
        return value.decode("ascii").strip()
    for npu, _ in sorted(mapping):
        name = "davinci" + str(npu)
        node = root / "dev" / name
        details = node.lstat()
        if not stat.S_ISCHR(details.st_mode):
            raise ObservationError("legacy physical device node is not a character device")
        major, minor = os.major(details.st_rdev), os.minor(details.st_rdev)
        if (major, minor) in identities:
            raise ObservationError("legacy device nodes share a kernel identity")
        identities.add((major, minor))
        lookup = root / "sys/dev/char" / (str(major) + ":" + str(minor))
        kernel = lookup.resolve(strict=True)
        if not kernel.is_relative_to(sys_devices) or kernel.name != name:
            raise ObservationError("legacy physical node has no matching kernel device")
        kernel_identity = kernel.stat()
        if read(kernel / "dev") != str(major) + ":" + str(minor):
            raise ObservationError("legacy kernel device numbers disagree")
        event = {}
        for line in read(kernel / "uevent").splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in event:
                raise ObservationError("legacy kernel device event is ambiguous")
            event[key] = value
        if (event.get("MAJOR") != str(major) or event.get("MINOR") != str(minor)
                or event.get("DEVNAME") != name):
            raise ObservationError("legacy kernel device name or numbers disagree")
        after_node = node.lstat()
        after_kernel = kernel.stat()
        if ((details.st_dev, details.st_ino, details.st_rdev, details.st_mode)
                != (after_node.st_dev, after_node.st_ino, after_node.st_rdev, after_node.st_mode)
                or lookup.resolve(strict=True) != kernel
                or (kernel_identity.st_dev, kernel_identity.st_ino) != (after_kernel.st_dev, after_kernel.st_ino)):
            raise ObservationError("legacy kernel device identity changed during collection")
        proof.append({"physical_device_id": npu, "major": major, "minor": minor, "kernel_name": name})
    after = boot_clock(root)
    if after["boot_id"] != before["boot_id"] or after["uptime_ms"] < before["uptime_ms"]:
        raise ObservationError("legacy host boot clock changed during identity verification")
    return {"authority": "a2-npu-id-and-kernel-char", "boot_id": before["boot_id"],
            "pci_binding_cross_verified": False, "devices": proof}


def parse_observation(info_text: str, mapping_text: str, *, identity: str,
                      collected_at: datetime | None = None, ttl_seconds=45, host_root="/") -> dict:
    """Parse explicit physical-ID tables or the kernel-verified 25.5.2/910B3 profile."""
    captured = collected_at or datetime.now(timezone.utc)
    if (not isinstance(identity, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity)
            or type(ttl_seconds) is not int or not 5 <= ttl_seconds <= 300):
        raise ObservationError("invalid observation identity or TTL")
    if any(len(value.encode("utf-8")) > MAX_OUTPUT_BYTES for value in (info_text, mapping_text)):
        raise ObservationError("host observation exceeds its size limit")
    header = next((" ".join(line.split()) for line in mapping_text.splitlines() if line.strip()), "")
    legacy = header == _LEGACY_HEADER
    mapping = _legacy_mapping(mapping_text) if legacy else _mapping(mapping_text)
    lines = [line.strip() for line in info_text.splitlines() if line.strip()]
    if not lines or not _BORDER.fullmatch(lines[-1]):
        raise ObservationError("npu-smi output is incomplete")
    devices, pending, legacy_buses = {}, None, set()
    seen_version = hardware_header = chip_header = process_header = False
    covered_cards, empty_cards, closed_cards = set(), set(), set()
    current_card = None
    for line in lines:
        if len(line) > 8192:
            raise ObservationError("npu-smi line exceeds its limit")
        if _BORDER.fullmatch(line):
            continue
        normalized = " ".join(line.replace("|", " ").split())
        if re.fullmatch(r"npu-smi [0-9]+(?:\.[0-9A-Za-z]+)+ Version: [0-9]+(?:\.[0-9A-Za-z]+)+", normalized):
            if seen_version:
                raise ObservationError("duplicate npu-smi version header")
            if legacy and normalized != "npu-smi 25.5.2 Version: 25.5.2":
                raise ObservationError("unsupported legacy npu-smi version")
            seen_version = True
            continue
        if normalized == "NPU Name Health Power(W) Temp(C) Hugepages-Usage(page)":
            if hardware_header:
                raise ObservationError("duplicate npu-smi device header")
            hardware_header = True
            continue
        if normalized == ("Chip Bus-Id AICore(%) Memory-Usage(MB) HBM-Usage(MB)" if legacy else
                          "Chip Phy-ID Bus-Id AICore(%) Memory-Usage(MB) HBM-Usage(MB)"):
            if legacy and chip_header:
                raise ObservationError("duplicate legacy npu-smi chip header")
            chip_header = True
            continue
        if normalized in {"NPU Chip Process id Process name Process memory(MB) Process id in container",
                           "NPU Chip Process id Process name Process memory(MB)"}:
            if process_header or pending or not devices:
                raise ObservationError("unexpected npu-smi process header")
            process_header = True
            continue
        if not seen_version or not hardware_header or not chip_header:
            raise ObservationError("npu-smi device headers are incomplete")
        if not process_header:
            top, bottom = _TOP.fullmatch(line), (_LEGACY_BOTTOM if legacy else _BOTTOM).fullmatch(line)
            if top and pending is None:
                pending = (_integer(top[1]), top[2], top[3], _number(top[4], 0, 10000), _number(top[5], -40, 200))
                continue
            if bottom and pending is not None:
                npu, model, health, power, temperature = pending
                chip = _integer(bottom[1])
                physical = npu if legacy else _integer(bottom[2])
                pair = (npu, chip)
                expected = mapping.get(pair)
                if pair in devices or expected is None or expected['physical_device_id'] != physical or expected['model_name'] != model:
                    raise ObservationError("npu-smi physical/logical identity does not match")
                bus = bottom[2 if legacy else 3]
                if legacy and bus.lower() in legacy_buses:
                    raise ObservationError("duplicate legacy single-chip PCI identity")
                if legacy:
                    legacy_buses.add(bus.lower())
                used = total = None
                hbm = bottom[5 if legacy else 6]
                if hbm not in {"-", "NA"}:
                    memory_parts = [part.strip() for part in hbm.split("/")]
                    used, total = [_integer(part, (2**53 - 1)//MIB) * MIB for part in memory_parts]
                    if not 0 < total or used > total:
                        raise ObservationError("npu-smi HBM observation is inconsistent")
                devices[pair] = {"device_id": str(physical), "device_index": physical, **expected,
                    "npu_id": npu, "chip_id": chip, "mapping_status": "verified", "uuid": "",
                    "pci_bus_id": bus, "utilization_percent": _number(bottom[3 if legacy else 4]),
                    "memory_total_bytes": total, "memory_used_bytes": used,
                    "memory_usage_percent": round(used/total*100, 3) if total else None,
                    "health": "healthy" if health == "OK" else "unhealthy" if health in {"Warning", "Alarm", "Critical"} else "unknown",
                    "temperature_celsius": temperature, "power_watts": power,
                    "process_pid_scope": "host", "processes_status": "unavailable", "processes": []}
                pending = None
                continue
            raise ObservationError("unsupported npu-smi hardware row")
        empty = re.fullmatch(r"\|\s*No running processes found in NPU (\d+)\s*\|", line)
        if empty:
            npu = _integer(empty[1])
            if npu in covered_cards or not any(pair[0] == npu for pair in devices):
                raise ObservationError("npu-smi empty-process coverage is ambiguous")
            empty_cards.add(npu)
        else:
            if not line.startswith("|") or not line.endswith("|"):
                raise ObservationError("unsupported npu-smi process row")
            cells = [cell.strip() for cell in line[1:-1].split("|")]
            if len(cells) not in {4, 5}:
                raise ObservationError("unsupported npu-smi process columns")
            pair = cells[0].split()
            if len(pair) != 2:
                raise ObservationError("unsupported npu-smi process identity")
            npu, chip = _integer(pair[0]), _integer(pair[1])
            device = devices.get((npu, chip))
            pid = _integer(cells[1], 2**31 - 1)
            if (device is None or pid == 0 or npu in empty_cards or not cells[2] or len(cells[2]) > 256
                    or any(ord(char) < 32 for char in cells[2])
                    or len(cells) == 5 and cells[4] != "NA" and not cells[4].isdecimal()):
                raise ObservationError("invalid npu-smi process observation")
            memory = _integer(cells[3], (2**53 - 1)//MIB) * MIB
            if len(device['processes']) >= MAX_PROCESSES or any(item['pid'] == pid for item in device['processes']):
                raise ObservationError("duplicate or oversized npu-smi process list")
            device['processes'].append({"pid": pid, "memory_used_bytes": memory})
        if current_card is not None and npu != current_card:
            closed_cards.add(current_card)
        if npu in closed_cards:
            raise ObservationError("npu-smi process groups are not contiguous")
        current_card = npu
        covered_cards.add(npu)
    if (not process_header or pending is not None or set(devices) != set(mapping)
            or covered_cards != {pair[0] for pair in devices}):
        raise ObservationError("npu-smi output does not cover every physical chip and process group")
    for device in devices.values():
        device['processes_status'] = 'ready'
    complete = all(device['health'] != 'unknown' and device['utilization_percent'] is not None
                   and device['memory_total_bytes'] is not None for device in devices.values())
    times = {"collected_at": captured.isoformat(), "expires_at": (captured+timedelta(seconds=ttl_seconds)).isoformat()}
    provenance = _legacy_node_proof(mapping, host_root) if legacy else None
    return {"inventory_schema": 2, "host_identity": identity, **times,
            **({"collector_profile": _LEGACY_PROFILE, "mapping_provenance": provenance} if legacy else {}),
            "npu_telemetry": {"status": "ready" if complete else "partial", "source": "host-npu-smi", **times,
                "memory_unit_source": "npu-smi-MB=1048576bytes", "errors": [],
                "devices": sorted(devices.values(), key=lambda device: device['device_index'])}}


def run_fixed(binary: Path, *, mapping=False, timeout=10) -> str:
    """Bound both command lifetime and output; stdout is never a shell command."""
    command = [str(binary), "info"] + (["-m"] if mapping else [])
    environment = {**os.environ, "LC_ALL": "C", "LANG": "C",
                   "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, env=environment, close_fds=True,
                               bufsize=0, start_new_session=os.name == "posix")
    chunks, errors = [], []
    def terminate():
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass
    def read():
        size = 0
        try:
            while chunk := process.stdout.read(65536):
                size += len(chunk)
                if size > MAX_OUTPUT_BYTES:
                    errors.append("size")
                    terminate()
                    break
                chunks.append(chunk)
        except OSError:
            errors.append("read")
    thread = threading.Thread(target=read, name="npu-smi-output", daemon=True)
    try:
        thread.start()
    except RuntimeError as error:
        # A task/thread limit must not leave the already-created query running.
        terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        process.stdout.close()
        raise ObservationError("npu-smi output reader could not be started") from error
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            errors.append("termination")
        errors.append("timeout")
    finally:
        drain_deadline = time.monotonic() + 2
        thread.join(timeout=1)
        if thread.is_alive():
            # A wrapper may exit while a descendant still holds the pipe. Do
            # not leave that query running after this collection has failed.
            errors.append("incomplete_output")
            terminate()
            thread.join(timeout=max(0, drain_deadline - time.monotonic()))
        process.stdout.close()
    if process.returncode != 0 or errors or thread.is_alive():
        raise ObservationError("fixed read-only npu-smi command failed or exceeded its collection budget")
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeError as error:
        raise ObservationError("npu-smi output is not UTF-8") from error


def _private_directory(path: Path) -> None:
    if not path.is_absolute() or path.resolve() != path:
        raise ObservationError("output directory must be an absolute real directory")
    for directory in (path, *path.parents):
        metadata = directory.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {0, os.geteuid()}
                or metadata.st_mode & 0o022 and not metadata.st_mode & stat.S_ISVTX):
            raise ObservationError("output directory ancestry is not trusted")


def write_snapshot(output: Path, value: dict) -> None:
    _private_directory(output.parent)
    if output.is_symlink() or output.exists() and not stat.S_ISREG(output.lstat().st_mode):
        raise ObservationError("snapshot target is not a regular file")
    body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(body) > MAX_OUTPUT_BYTES:
        raise ObservationError("snapshot exceeds the file budget")
    descriptor, temporary = tempfile.mkstemp(prefix=".npu-observation-", dir=output.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        directory_fd = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _trusted_binary(candidate: Path) -> Path:
    if not candidate.is_absolute():
        raise ObservationError("npu-smi executable must use an absolute trusted path")
    binary = candidate.resolve(strict=True)
    for directory in {*candidate.parents, *binary.parents}:
        details = directory.lstat()
        if details.st_uid != 0 or (not stat.S_ISLNK(details.st_mode) and details.st_mode & 0o022):
            raise ObservationError("npu-smi executable ancestry must be owned and protected by root")
    link = candidate.lstat()
    details = binary.stat()
    if (link.st_uid != 0 or not stat.S_ISREG(details.st_mode) or details.st_uid != 0
            or details.st_mode & 0o022 or not details.st_mode & 0o111 or not os.access(binary, os.X_OK)):
        raise ObservationError("npu-smi executable must be a protected root-owned executable")
    return binary


def choose_binary(explicit: Path | None = None) -> Path:
    candidates = [explicit] if explicit is not None else [Path("/usr/local/sbin/npu-smi"), Path("/usr/local/Ascend/driver/tools/npu-smi")]
    for candidate in candidates:
        try:
            return _trusted_binary(candidate)
        except (OSError, ObservationError):
            continue
    raise ObservationError("no supported protected npu-smi executable was found")


@contextmanager
def collection_slot(output: Path):
    """Do not overlap this output's timer and direct CLI invocations or queue."""
    if os.name != "posix":
        raise ObservationError("the host collector requires a Linux host")
    import fcntl
    _private_directory(output.parent)
    descriptor = os.open(output.parent / ".collector.lock",
                         os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1
                or details.st_uid not in {0, os.geteuid()} or details.st_mode & 0o077):
            raise ObservationError("collector lock must be a protected regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        # Keep the inode in place; unlinking a lock could admit a third writer.
        os.close(descriptor)


def collect_once(output: Path, *, binary: Path | None = None, ttl_seconds=45) -> int:
    with collection_slot(output) as acquired:
        if not acquired:
            return 0  # Another owner produces the snapshot; do not overwrite it.
        return _collect_locked(output, binary=binary, ttl_seconds=ttl_seconds)


def _collect_locked(output: Path, *, binary: Path | None, ttl_seconds: int) -> int:
    captured = datetime.now(timezone.utc)
    try:
        identity = host_identity()
        clock = boot_clock()
        binary = choose_binary(binary)
        info = run_fixed(binary)
        mapping = run_fixed(binary, mapping=True)
        if host_identity() != identity:
            raise ObservationError("host identity changed during collection")
        value = parse_observation(info, mapping, identity=identity, collected_at=captured, ttl_seconds=ttl_seconds)
        current_clock = boot_clock()
        if current_clock["boot_id"] != clock["boot_id"] or current_clock["uptime_ms"] < clock["uptime_ms"]:
            raise ObservationError("host boot clock changed during collection")
        value["sample_clock"] = clock
        write_snapshot(output, value)
        return 0
    except (OSError, ObservationError, ValueError):
        # The sampler's failed run invalidates the snapshot instead of leaving
        # an old successful value in place with an apparently current mtime.
        try:
            identity = host_identity()
            value = {"inventory_schema": 2, "host_identity": identity, "collected_at": captured.isoformat(),
                "expires_at": (captured+timedelta(seconds=ttl_seconds)).isoformat(),
                "npu_telemetry": {"status": "error", "source": "host-npu-smi", "devices": [],
                    "errors": [{"source": "host-npu-smi", "code": "collection_failed",
                                "message": "宿主 NPU 只读采集失败，硬件资源状态未知"}]}}
            write_snapshot(output, value)
        except (OSError, ValueError):
            pass
        print("Host NPU observation failed; no trusted current device data was produced.", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=None, help="explicit trusted absolute npu-smi path; otherwise check the two fixed standard locations")
    parser.add_argument("--ttl-seconds", type=int, default=45, choices=range(5, 301), metavar="5..300")
    args = parser.parse_args()
    try:
        return collect_once(args.output, binary=args.binary, ttl_seconds=args.ttl_seconds)
    except (OSError, ValueError):
        print("Host NPU observation failed; the protected collection slot is unavailable.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
