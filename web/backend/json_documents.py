"""Shared validation for lossless, finite native configuration documents."""
import json


def validate_json_document(value) -> None:
    try:
        json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ValueError("配置必须是有效的有限 JSON，不能包含 NaN、Infinity 或无效 Unicode") from exc
