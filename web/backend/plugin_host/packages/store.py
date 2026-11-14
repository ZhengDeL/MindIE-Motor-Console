"""Immutable package inventory and revisioned next-start selection."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any
from uuid import uuid4

from ...version import CONSOLE_VERSION
from .archive import MAX_PACKAGE_BYTES, inspect_archive, member_name
from .errors import PackageConflict, PackageNotFound, PackageStoreUnavailable, PluginPackageError
from .files import atomic_write, ensure_directory, linked, parents, publish_new, read_regular, store_lock
from .manifest import (RESERVED_IDS, canonical_json, check_dependency_versions, declaration,
                       json_document, validate_manifest)
from .limits import MAX_PACKAGES, MAX_ACTIVE_PLUGINS, MAX_STATE_BYTES, MAX_CONFIGURATION_BYTES, MAX_RESET_EVENTS

_DIGEST = re.compile(r"[a-f0-9]{64}")
_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")


def _digest(value):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise PluginPackageError("Package digest must be a lowercase SHA-256 value")
    return value


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise PluginPackageError("Plugin ID is invalid")
    return value


def _revision(value):
    if type(value) is not int or value < 0:
        raise PluginPackageError("Expected plugin state revision must be a nonnegative integer")
    return value


def _configuration(value):
    if not isinstance(value, dict):
        raise PluginPackageError("Plugin configuration must be a JSON object")
    return json_document(canonical_json(value, maximum=MAX_CONFIGURATION_BYTES), maximum=MAX_CONFIGURATION_BYTES)


@dataclass(frozen=True)
class PackageProfile:
    digest: str
    _store: Any
    _manifest_json: bytes
    _configuration_json: bytes

    @property
    def manifest(self) -> dict:
        return json_document(self._manifest_json)

    @property
    def configuration(self) -> dict:
        return json_document(self._configuration_json, maximum=MAX_CONFIGURATION_BYTES)

    @property
    def configuration_digest(self) -> str:
        return hashlib.sha256(self._configuration_json).hexdigest()

    def declaration(self):
        return declaration(self.manifest)

    def _verified_archive(self):
        record, files = self._store._archive(self.digest)
        if canonical_json(record["manifest"]) != self._manifest_json:
            raise PluginPackageError("Active package metadata no longer matches the selected artifact")
        return record, files

    def load_factory(self):
        from .loader import trusted_factory
        return trusted_factory(self)

    def read_resource(self, path: str) -> bytes:
        name = member_name(path)
        _, files = self._verified_archive()
        if name not in files:
            raise PackageNotFound("Plugin resource does not exist")
        return files[name]


class PackageStore:
    def __init__(self, root: str | Path, *, host_version: str = CONSOLE_VERSION, reserved_ids=None, recover=True):
        self.root = Path(root).absolute()
        self.host_version = host_version
        self.reserved_ids = RESERVED_IDS | frozenset(reserved_ids or ())
        self._metadata_cache = {}
        self._cache_guard = threading.RLock()
        self.recovery_enabled = recover is True
        # Read-only status of an unconfigured host must not create directories.
        parents(self.root)
        if self.recovery_enabled and self.package_directory.exists():
            with store_lock(self.root):
                self._recover_stages()

    @property
    def package_directory(self):
        return self.root / "packages"

    def _archive(self, digest: str, *, check_compatibility=True):
        digest = _digest(digest)
        try:
            raw = read_regular(self.package_directory / (digest + ".qhplugin"), MAX_PACKAGE_BYTES)
        except FileNotFoundError as exc:
            raise PackageNotFound("Plugin package was not imported") from exc
        if hashlib.sha256(raw).hexdigest() != digest:
            raise PluginPackageError("Imported plugin package bytes no longer match their SHA-256 identity")
        return inspect_archive(raw, host_version=self.host_version, reserved_ids=self.reserved_ids,
                               check_compatibility=check_compatibility)

    def inspect(self, digest: str) -> dict:
        return self._archive(digest)[0]

    def _paths(self):
        parents(self.package_directory)
        if not self.package_directory.exists():
            return []
        paths = []
        try:
            with os.scandir(self.package_directory) as entries:
                for entry in entries:
                    if len(paths) >= MAX_PACKAGES:
                        raise PluginPackageError("Plugin inventory exceeds the supported package count")
                    # DirEntry.stat can omit the hard-link count on Windows.
                    info = Path(entry.path).lstat()
                    if (not entry.name.endswith(".qhplugin") or not _DIGEST.fullmatch(entry.name[:-9])
                            or linked(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                        raise PackageStoreUnavailable("Plugin inventory contains an unexpected or linked file")
                    paths.append(Path(entry.path))
        except OSError as exc:
            raise PackageStoreUnavailable("Plugin inventory could not be read") from exc
        return sorted(paths)

    def _state(self, *, validate_selection=True) -> dict:
        try:
            raw = read_regular(self.root / "active.json", MAX_STATE_BYTES)
        except FileNotFoundError:
            return {"schemaVersion": 1, "revision": 0, "selected": []}
        value = json_document(raw, maximum=MAX_STATE_BYTES)
        if (not isinstance(value, dict) or set(value) - {"schemaVersion", "revision", "selected", "updated_at", "recovery_events"}
                or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1
                or type(value.get("revision")) is not int or value["revision"] < 0
                or not isinstance(value.get("selected"), list) or len(value["selected"]) > MAX_ACTIVE_PLUGINS):
            raise PluginPackageError("Plugin activation state has an invalid schema")
        identifiers = set()
        if "updated_at" in value:
            stamp = value["updated_at"]
            try:
                if not isinstance(stamp, str) or len(stamp) > 64 or datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError("missing timezone")
            except ValueError as exc:
                raise PluginPackageError("Plugin activation timestamp is invalid") from exc
        for entry in value["selected"]:
            if not isinstance(entry, dict) or set(entry) != {"id", "digest", "trust_code", "configuration"}:
                raise PluginPackageError("Plugin activation entry has an invalid schema")
            identifier = _identifier(entry["id"])
            if (identifier in identifiers or type(entry["trust_code"]) is not bool
                    or validate_selection and (identifier in self.reserved_ids or entry["trust_code"] is not True)):
                raise PluginPackageError("Plugin activation entry is duplicated, reserved or untrusted")
            identifiers.add(identifier)
            _digest(entry["digest"])
            _configuration(entry["configuration"])
        recoveries = value.get("recovery_events", [])
        if not isinstance(recoveries, list) or len(recoveries) > MAX_RESET_EVENTS:
            raise PluginPackageError("Plugin selection recovery history exceeds its supported shape")
        for event in recoveries:
            required = {"id", "action", "from_revision", "to_revision", "created_at"}
            if (not isinstance(event, dict) or not required <= event.keys()
                    or set(event) - required - {"backup_file", "backup_sha256"}
                    or not isinstance(event["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", event["id"])
                    or event["action"] != "reset-selection" or type(event["from_revision"]) is not int
                    or type(event["to_revision"]) is not int or event["from_revision"] < 0
                    or event["to_revision"] != event["from_revision"] + 1
                    or not isinstance(event["created_at"], str) or len(event["created_at"]) > 64):
                raise PluginPackageError("Plugin selection recovery history contains an invalid event")
            if "backup_file" in event or "backup_sha256" in event:
                if (not isinstance(event.get("backup_sha256"), str) or not _DIGEST.fullmatch(event["backup_sha256"])
                        or event.get("backup_file") != str(event["from_revision"]) + "-" + event["backup_sha256"] + ".json"):
                    raise PluginPackageError("Plugin recovery backup reference is invalid")
            try:
                if datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError("timezone")
            except ValueError as exc:
                raise PluginPackageError("Plugin selection recovery event timestamp is invalid") from exc
        return value

    @staticmethod
    def _public_state(state):
        return {"schemaVersion": 1, "revision": state["revision"], "restart_required": True,
                "selected": [{"id": item["id"], "digest": item["digest"], "trust_code": item["trust_code"],
                              "configuration_digest": hashlib.sha256(canonical_json(item["configuration"])).hexdigest(),
                              "configured_keys": sorted(item["configuration"])} for item in state["selected"]],
                "recovery_events": state.get("recovery_events", [])}

    def _recover_stages(self):
        if self.recovery_enabled:
            from .recovery import recover_stages
            recover_stages(self)

    def list(self) -> dict:
        with self._cache_guard:
            if not self.root.exists():
                parents(self.root)
                return {**self._public_state({"schemaVersion": 1, "revision": 0, "selected": []}), "packages": [],
                        "max_package_bytes": MAX_PACKAGE_BYTES, "max_packages": MAX_PACKAGES,
                        "import_executes_code": False, "activation_requires_restart": True}
            with store_lock(self.root):
                self._recover_stages()
                return self._list_unlocked()

    def _list_unlocked(self) -> dict:
        state = self._state()
        packages = []
        selected = {entry["digest"] for entry in state["selected"]}
        paths = self._paths()
        present = {path.name for path in paths}
        self._metadata_cache = {name: value for name, value in self._metadata_cache.items() if name in present}
        for path in paths:
            info = path.lstat()
            key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            cached = self._metadata_cache.get(path.name)
            if cached is None or cached[0] != key:
                record = self._archive(path.name[:-9], check_compatibility=False)[0]
                try:
                    validate_manifest(record["manifest"], host_version=self.host_version, reserved_ids=self.reserved_ids)
                    record["compatible_with_host"] = True
                except PluginPackageError:
                    record["compatible_with_host"] = False
                self._metadata_cache[path.name] = (key, canonical_json(record))
            else:
                record = json_document(cached[1], maximum=MAX_STATE_BYTES)
            packages.append({**record, "selected_next_start": record["digest"] in selected,
                             "state": "selected-next-start" if record["digest"] in selected else "imported"})
        from .recovery import recovery_summary
        return {**self._public_state(state), "packages": packages, "recovery": recovery_summary(self),
                "max_package_bytes": MAX_PACKAGE_BYTES, "max_packages": MAX_PACKAGES,
                "import_executes_code": False, "activation_requires_restart": True}

    def import_bytes(self, raw: bytes) -> dict:
        record, _ = inspect_archive(raw, host_version=self.host_version, reserved_ids=self.reserved_ids)
        digest = record["digest"]
        with store_lock(self.root):
            ensure_directory(self.package_directory)
            self._recover_stages()
            destination = self.package_directory / (digest + ".qhplugin")
            if destination.exists() or destination.is_symlink():
                if read_regular(destination, MAX_PACKAGE_BYTES) != raw:
                    raise PluginPackageError("Package identity collision or damaged immutable artifact")
            else:
                if len(self._paths()) >= MAX_PACKAGES:
                    raise PluginPackageError("Plugin package inventory is full; preserve rollback artifacts before maintenance")
                try:
                    publish_new(destination, raw)
                except OSError as exc:
                    raise PackageStoreUnavailable("Plugin package could not be retained") from exc
            # Verify the actual persisted bytes, not just the request buffer.
            self.inspect(digest)
        return {**record, "state": "imported", "code_executed": False, "restart_required": False}

    def activate(self, identifier: str, digest: str, *, expected_revision: int, trust_code: bool,
                 configuration: dict | None = None) -> dict:
        identifier, digest = _identifier(identifier), _digest(digest)
        _revision(expected_revision)
        if trust_code is not True:
            raise PluginPackageError("Explicit trust in this package's code is required before next-start activation")
        config = _configuration({} if configuration is None else configuration)
        with store_lock(self.root):
            current = self._state()
            if current["revision"] != expected_revision:
                raise PackageConflict("Plugin selection changed; reload its revision before saving")
            record = self.inspect(digest)
            if record["manifest"]["id"] != identifier:
                raise PluginPackageError("Package ID does not match the activation target")
            values = [entry for entry in current["selected"] if entry["id"] != identifier]
            values.append({"id": identifier, "digest": digest, "trust_code": True, "configuration": config})
            if len(values) > MAX_ACTIVE_PLUGINS:
                raise PluginPackageError("Active plugin selection exceeds its limit")
            candidate = {"schemaVersion": 1, "revision": expected_revision + 1, "selected": sorted(values, key=lambda item: item["id"]),
                         "updated_at": datetime.now(timezone.utc).isoformat()}
            if "recovery_events" in current:
                candidate["recovery_events"] = current["recovery_events"]
            self._validate_profiles(candidate)
            atomic_write(self.root / "active.json", canonical_json(candidate, maximum=MAX_STATE_BYTES))
            return self._public_state(candidate)

    def deactivate(self, identifier: str, *, expected_revision: int) -> dict:
        identifier = _identifier(identifier)
        _revision(expected_revision)
        with store_lock(self.root):
            current = self._state()
            if current["revision"] != expected_revision:
                raise PackageConflict("Plugin selection changed; reload its revision before saving")
            values = [entry for entry in current["selected"] if entry["id"] != identifier]
            if len(values) == len(current["selected"]):
                raise PackageNotFound("Plugin is not selected for the next start")
            candidate = {"schemaVersion": 1, "revision": expected_revision + 1, "selected": values,
                         "updated_at": datetime.now(timezone.utc).isoformat()}
            if "recovery_events" in current:
                candidate["recovery_events"] = current["recovery_events"]
            self._validate_profiles(candidate)
            atomic_write(self.root / "active.json", canonical_json(candidate, maximum=MAX_STATE_BYTES))
            return self._public_state(candidate)

    def _validate_profiles(self, state):
        from ..builtin import builtin_manifests
        from ..registry import PluginRegistry
        records = []
        profiles = []
        for entry in state["selected"]:
            record, _files = self._archive(entry["digest"])
            manifest = record["manifest"]
            if manifest["id"] != entry["id"] or entry["trust_code"] is not True:
                raise PluginPackageError("Selected plugin metadata or code trust does not match its immutable artifact")
            records.append(manifest)
            profiles.append(PackageProfile(entry["digest"], self, canonical_json(manifest), canonical_json(entry["configuration"])))
        builtin = builtin_manifests()
        declarations = [*builtin, *(profile.declaration() for profile in profiles)]
        # Activation validates the complete next-start graph without executing
        # any code. Host-specific disabled choices are additionally checked by
        # the actual application before it imports the selected factories.
        try:
            registry = PluginRegistry(declarations)
            check_dependency_versions(records, {item.id: item.version for item in declarations})
        except ValueError as exc:
            raise PluginPackageError("Selected plugin dependency or compatibility contract is invalid: " + str(exc)) from exc
        positions = {identifier: index for index, identifier in enumerate(registry.order)}
        profiles.sort(key=lambda profile: positions[profile.manifest["id"]])
        return tuple(profiles)

    def active_profiles(self) -> tuple[PackageProfile, ...]:
        # Atomic replace gives readers one complete selection; imported package
        # files are content-addressed and never changed by this implementation.
        if self.recovery_enabled and self.root.exists():
            with store_lock(self.root):
                self._recover_stages()
                return self._validate_profiles(self._state())
        return self._validate_profiles(self._state())

    def reset_selection(self, *, expected_revision: int, confirm_clear_selection: bool) -> dict:
        """Explicit CLI recovery: clear pointers, retain packages and running code.

        This operation does not inspect any package or resolve compatibility.
        Its bounded recovery event is committed in the same atomic state write.
        """
        _revision(expected_revision)
        if confirm_clear_selection is not True:
            raise PluginPackageError("Clearing all next-start selections requires explicit confirmation")
        with store_lock(self.root):
            current = self._state(validate_selection=False)
            if current["revision"] != expected_revision:
                raise PackageConflict("Plugin selection changed; reload its revision before recovery")
            history = current.get("recovery_events", [])
            if len(history) >= MAX_RESET_EVENTS:
                raise PluginPackageError("Selection recovery history is full; archive the local state before continuing")
            backup_file, backup_digest = self._selection_snapshot(current)
            stamp = datetime.now(timezone.utc).isoformat()
            event = {"id": uuid4().hex, "action": "reset-selection", "from_revision": expected_revision,
                     "to_revision": expected_revision + 1, "created_at": stamp,
                     "backup_file": backup_file, "backup_sha256": backup_digest}
            candidate = {"schemaVersion": 1, "revision": expected_revision + 1, "selected": [],
                         "updated_at": stamp, "recovery_events": [*history, event]}
            atomic_write(self.root / "active.json", canonical_json(candidate, maximum=MAX_STATE_BYTES))
            return self._public_state(candidate)

    def _selection_snapshot(self, current):
        """Retain one private, content-addressed state snapshot before reset.

        The caller holds store_lock. No package is inspected and no prior
        snapshot is overwritten or silently pruned to make room.
        """
        directory = self.root / "selection-backups"
        ensure_directory(directory)
        raw = canonical_json(current, maximum=MAX_STATE_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        name = str(current["revision"]) + "-" + digest + ".json"
        found = set()
        with os.scandir(directory) as entries:
            for entry in entries:
                if len(found) >= MAX_RESET_EVENTS:
                    raise PackageStoreUnavailable("Plugin selection backup limit reached; archive the private snapshots explicitly")
                match = re.fullmatch(r"(0|[1-9][0-9]*)-([a-f0-9]{64})\.json", entry.name)
                if match is None:
                    raise PackageStoreUnavailable("Plugin selection backup directory contains an unexpected file")
                path = Path(entry.path)
                info = path.lstat()
                if linked(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_STATE_BYTES:
                    raise PackageStoreUnavailable("Plugin selection backup must be a bounded private regular file without links")
                retained = read_regular(path, MAX_STATE_BYTES)
                if hashlib.sha256(retained).hexdigest() != match.group(2):
                    raise PackageStoreUnavailable("Plugin selection backup no longer matches its retained SHA-256")
                if entry.name == name and retained != raw:
                    raise PackageStoreUnavailable("Plugin selection backup identity collision")
                found.add(entry.name)
        if name not in found:
            if len(found) >= MAX_RESET_EVENTS:
                raise PackageStoreUnavailable("Plugin selection backup limit reached; archive the private snapshots explicitly")
            publish_new(directory / name, raw)
        # Verify the actual durable snapshot before clearing any pointer.
        if read_regular(directory / name, MAX_STATE_BYTES) != raw:
            raise PackageStoreUnavailable("Plugin selection snapshot was not retained intact; pointers were not cleared")
        return name, digest
