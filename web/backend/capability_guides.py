"""Bundled, read-only feature guides for the recorded native Motor contract.

Only this fixed package file is read. API callers cannot supply a path, URL or
executable example; rendering or copying a guide never changes service config.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path


_CATALOG = json.loads(Path(__file__).with_name("capability_guides.json").read_text(encoding="utf-8"))


def capability_guide(name: str, motor_commit: str) -> dict | None:
    if motor_commit != _CATALOG["motor_commit"]:
        return None
    value = _CATALOG["guides"].get(name)
    if value is None:
        return None
    return {
        "schema_version": _CATALOG["schema_version"],
        "motor_commit": _CATALOG["motor_commit"],
        **copy.deepcopy(value),
    }
