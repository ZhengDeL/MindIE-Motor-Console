"""Public typing contract for community factories; no host internals required.

Factories are structurally typed. Authors may use an equivalent local Protocol
under TYPE_CHECKING, so the package has no runtime dependency on this module's
source/container import prefix.
"""
from __future__ import annotations

from typing import Any, Callable, NotRequired, Protocol, TypedDict


class CommunityPluginContextV1(Protocol):
    api_version: int
    plugin_id: str
    def require_service(self, name: str, expected_type: type | tuple[type, ...] | None = None) -> Any: ...
    def mount_router(self, router: Any, **options: Any) -> None: ...
    def provide(self, name: str, version: int, value: Any) -> Any: ...
    def consume(self, name: str, version: int) -> Any: ...
    def on_shutdown(self, callback: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None: ...


class TableColumnV1(TypedDict):
    key: str
    label: str


class StatusTableV1(TypedDict):
    columns: list[TableColumnV1]
    rows: list[dict[str, str | int | float | bool | None]]
    title: NotRequired[str]
    summary: NotRequired[str]
    updated_at: NotRequired[str]


def status_table_schema() -> dict:
    """Machine-readable shape; renderer additionally checks unique column keys."""
    scalar = {"anyOf": [
        {"type": "null"}, {"type": "boolean"}, {"type": "string", "maxLength": 4096},
        {"type": "integer", "minimum": -9007199254740991, "maximum": 9007199254740991},
        {"type": "number", "not": {"type": "integer"}},
    ]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Qinghuan status-table-v1", "type": "object", "required": ["columns", "rows"],
        "properties": {
            "title": {"type": "string", "maxLength": 200},
            "summary": {"type": "string", "maxLength": 2000},
            "updated_at": {"type": "string", "maxLength": 100},
            "columns": {"type": "array", "minItems": 1, "maxItems": 32,
                        "items": {"type": "object", "required": ["key", "label"], "properties": {
                            "key": {"type": "string", "pattern": "^[a-zA-Z][a-zA-Z0-9_.-]{0,63}$"},
                            "label": {"type": "string", "minLength": 1, "maxLength": 100}}}},
            "rows": {"type": "array", "maxItems": 1000,
                     "items": {"type": "object", "maxProperties": 32, "additionalProperties": scalar}},
        },
        "x-runtime-rules": ["column keys must be unique", "missing declared cells render as null",
                            "UTF-8 response must not exceed 1 MiB", "values are text, never executable HTML"],
    }
