"""Writable deployment paths must belong to this MindIE-Motor Console installation."""
from pathlib import Path


def require_product_path(value, purpose: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(purpose + " must be an absolute dedicated platform path")
    parts = [part.casefold() for part in path.parts]
    foreign = {"hx", "shhc", "hx-platform", "hx-platform-agents", "hx-model-platform"}
    if any(part in foreign for part in parts):
        raise ValueError(purpose + " must not reuse a directory reserved for another product")
    if not any(part in {"qinghuan", "motor-console"} or part.startswith(("motor-console-", "qinghuan-")) or part.endswith("-qh") for part in parts):
        raise ValueError(purpose + " must use a dedicated platform installation directory, for example /opt/motor-console")
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError(purpose + " must not traverse a symbolic link")
    return path.resolve()
