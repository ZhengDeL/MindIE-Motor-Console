"""Bounded recovery of this store's interrupted immutable-package publications.

Call only while holding the store's cross-process writer lock. Recovery stages
data; it never chooses a plugin or executes its code.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat

from .archive import inspect_archive
from .errors import PackageStoreUnavailable, PluginPackageError
from .files import (atomic_write, ensure_directory, linked, parents, read_regular, _sync_directory)
from .limits import MAX_PACKAGE_BYTES, MAX_PACKAGES, MAX_RECOVERY_EVENTS, MAX_QUARANTINE_FILES
from .manifest import canonical_json, json_document

_STAGE = re.compile(r"\.qhplugin-import-([0-9a-f]{32})\.tmp")
_PACKAGE = re.compile(r"([0-9a-f]{64})\.qhplugin")
_QUARANTINE = re.compile(r"([0-9a-f]{32})\.partial")
_ACTIONS = {"recover-import", "complete-import", "remove-duplicate-stage", "quarantine-stage"}
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _same_file(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _info(path):
    parents(path.parent)
    info = path.lstat()
    if linked(info) or not stat.S_ISREG(info.st_mode):
        raise PackageStoreUnavailable("Interrupted plugin publication contains a link or special file; manual inspection is required")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise PackageStoreUnavailable("Interrupted plugin publication has another owner; use the platform's UID for maintenance")
    return info


def _events(store):
    path = store.root / "recovery.json"
    try:
        value = json_document(read_regular(path, 262144), maximum=262144)
    except FileNotFoundError:
        return []
    if (not isinstance(value, dict) or set(value) != {"schemaVersion", "events"} or value["schemaVersion"] != 1
            or not isinstance(value["events"], list) or len(value["events"]) > MAX_RECOVERY_EVENTS):
        raise PackageStoreUnavailable("Plugin recovery journal has an invalid schema")
    seen = set()
    for event in value["events"]:
        if (not isinstance(event, dict) or set(event) != {"id", "stage", "action", "status", "digest", "created_at", "reason"}
                or not isinstance(event["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", event["id"])
                or event["id"] in seen or not isinstance(event["stage"], str) or not _STAGE.fullmatch(event["stage"])
                or not isinstance(event["action"], str) or event["action"] not in _ACTIONS
                or not isinstance(event["status"], str) or event["status"] not in {"started", "complete"}
                or not isinstance(event["digest"], str) or not _DIGEST.fullmatch(event["digest"])
                or not isinstance(event["reason"], str) or len(event["reason"]) > 80
                or not isinstance(event["created_at"], str) or len(event["created_at"]) > 64):
            raise PackageStoreUnavailable("Plugin recovery journal contains an invalid event")
        seen.add(event["id"])
        try:
            if datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")).tzinfo is None:
                raise ValueError("timezone")
        except ValueError as exc:
            raise PackageStoreUnavailable("Plugin recovery event timestamp is invalid") from exc
    return value["events"]


def _save(store, events):
    atomic_write(store.root / "recovery.json", canonical_json({"schemaVersion": 1, "events": events}, maximum=262144))


def _quarantine_inventory(store):
    directory = store.root / "quarantine"
    parents(directory)
    if not directory.exists():
        return []
    result = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if len(result) >= MAX_QUARANTINE_FILES or not _QUARANTINE.fullmatch(entry.name):
                raise PackageStoreUnavailable("Plugin quarantine requires explicit maintenance; no files were removed")
            info = _info(Path(entry.path))
            if info.st_nlink != 1 or info.st_size > MAX_PACKAGE_BYTES:
                raise PackageStoreUnavailable("Plugin quarantine file identity is invalid")
            result.append({"file": entry.name, "size_bytes": info.st_size})
    return sorted(result, key=lambda item: item["file"])


def recovery_summary(store):
    events = _events(store)
    return {"events": events, "quarantine": _quarantine_inventory(store),
            "automatic_code_execution": False, "selection_changed": False}


def _verify_raw(store, raw):
    return inspect_archive(raw, host_version=store.host_version, reserved_ids=store.reserved_ids,
                           check_compatibility=False)[0]


def _complete_event(store, events, event):
    if event["status"] != "complete":
        event["status"] = "complete"
        _save(store, events)


def recover_stages(store):
    """Recover exact generated names only, preserving unverifiable data."""
    directory = store.package_directory
    parents(directory)
    if not directory.exists():
        return
    events = _events(store)
    names, stages, package_paths = [], [], []
    with os.scandir(directory) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) > MAX_PACKAGES + MAX_PACKAGES:
                raise PackageStoreUnavailable("Plugin publication directory exceeds its recovery scan budget")
            if _STAGE.fullmatch(entry.name):
                stages.append(Path(entry.path))
            elif _PACKAGE.fullmatch(entry.name):
                package_paths.append(Path(entry.path))
            else:
                # An arbitrary .tmp or another user's file is never swept.
                raise PackageStoreUnavailable("Plugin inventory contains an unrecognized file; explicit maintenance is required")
    if len(package_paths) > MAX_PACKAGES or len(stages) > MAX_PACKAGES:
        raise PackageStoreUnavailable("Plugin publication inventory exceeds its recovery limit")
    by_id = {event["id"]: event for event in events}
    for stage in sorted(stages):
        identifier = _STAGE.fullmatch(stage.name).group(1)
        info = _info(stage)
        if info.st_nlink not in {1, 2} or info.st_size > MAX_PACKAGE_BYTES:
            raise PackageStoreUnavailable("Interrupted plugin publication has an unexpected size or link count")
        matches = [path for path in package_paths if _same_file(_info(path), info)] if info.st_nlink == 2 else []
        if info.st_nlink == 2 and (len(matches) != 1 or _info(matches[0]).st_nlink != 2):
            raise PackageStoreUnavailable("Interrupted plugin publication has an external or ambiguous hard link")
        raw = read_regular(stage, MAX_PACKAGE_BYTES, expected_links=info.st_nlink)
        digest = hashlib.sha256(raw).hexdigest()
        target = directory / (digest + ".qhplugin")
        if info.st_nlink == 2 and matches[0] != target:
            raise PackageStoreUnavailable("Interrupted publication target does not match its actual SHA-256")
        try:
            _verify_raw(store, raw)
            valid = True
        except PluginPackageError:
            valid = False
        if info.st_nlink == 2 and not valid:
            raise PackageStoreUnavailable("Linked plugin publication is not a complete valid artifact; both files were preserved")
        if not valid:
            action, reason = "quarantine-stage", "incomplete_or_invalid_source_archive"
        elif info.st_nlink == 2:
            action, reason = "complete-import", "interrupted_after_atomic_publication"
        elif target.exists() or target.is_symlink():
            target_info = _info(target)
            if target_info.st_nlink != 1 or read_regular(target, MAX_PACKAGE_BYTES) != raw:
                raise PackageStoreUnavailable("Interrupted publication conflicts with an existing immutable artifact")
            action, reason = "remove-duplicate-stage", "identical_artifact_already_retained"
        elif len(package_paths) >= MAX_PACKAGES:
            action, reason = "quarantine-stage", "package_inventory_capacity"
        else:
            action, reason = "recover-import", "interrupted_before_atomic_publication"
        event = by_id.get(identifier)
        if event is None:
            if len(events) >= MAX_RECOVERY_EVENTS:
                raise PackageStoreUnavailable("Plugin recovery journal is full; archive it explicitly before continuing")
            event = {"id": identifier, "stage": stage.name, "action": action, "status": "started", "digest": digest,
                     "created_at": datetime.now(timezone.utc).isoformat(), "reason": reason}
            events.append(event)
            by_id[identifier] = event
            _save(store, events)
        elif event["digest"] != digest or event["stage"] != stage.name:
            raise PackageStoreUnavailable("Interrupted publication no longer matches its recovery journal")
        # A prior recover-import may now be observed after its link was made.
        if action == "quarantine-stage":
            quarantine = store.root / "quarantine"
            existing = _quarantine_inventory(store)
            if len(existing) >= MAX_QUARANTINE_FILES:
                raise PackageStoreUnavailable("Plugin quarantine is full; no staged data was deleted")
            ensure_directory(quarantine)
            destination = quarantine / (identifier + ".partial")
            if destination.exists() or destination.is_symlink():
                raise PackageStoreUnavailable("Quarantine destination already contains another file")
            # Both paths are inside the verified private store, and all store
            # writers hold this lock. Rename preserves the complete original
            # file without a second crash-sensitive hard-link interval.
            os.rename(stage, destination)
            _sync_directory(quarantine)
            _sync_directory(directory)
        else:
            if action == "recover-import":
                os.link(stage, target, follow_symlinks=False)
                package_paths.append(target)
            stage.unlink()
            _sync_directory(directory)
        _complete_event(store, events, event)
    # A crash can occur between the named-file transaction and journal commit.
    for event in events:
        if event["status"] == "complete":
            continue
        stage = directory / event["stage"]
        if stage.exists() or stage.is_symlink():
            continue
        target = (store.root / "quarantine" / (event["id"] + ".partial") if event["action"] == "quarantine-stage"
                  else directory / (event["digest"] + ".qhplugin"))
        data = read_regular(target, MAX_PACKAGE_BYTES)
        if hashlib.sha256(data).hexdigest() != event["digest"]:
            raise PackageStoreUnavailable("Completed recovery target no longer matches its journal digest")
        if event["action"] != "quarantine-stage":
            _verify_raw(store, data)
        _complete_event(store, events, event)
