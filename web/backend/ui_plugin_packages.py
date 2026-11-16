"""Bounded, immutable UI artifacts and explicit next-start provider selection.

UI code is trusted browser code. Importing an archive validates bytes only;
selection never evaluates JavaScript or changes a running browser graph.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any
import zipfile

from .plugin_host.packages.files import atomic_write, ensure_directory, publish_new, read_regular, store_lock
from .plugin_host.packages.manifest import canonical_json, json_document
from .plugin_host.packages.errors import PluginPackageError

SDK_VERSION = "1.0.0"
DEFAULT_ID = "console.ui.default"
# Keep aligned with the public UI SDK; reference component slots are not kernel requirements.
UI_KERNEL_SLOTS = frozenset({"ui.appearance", "ui.theme", "ui.shell", "ui.i18n", "ui.notifications", "ui.button", "ui.actions"})
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_ENTRY_BYTES = 1024 * 1024
MAX_FILES = 64
MAX_PACKAGES = 64
MAX_SELECTED = 16
MAX_STATE_BYTES = 256 * 1024
_ID = re.compile(r"[a-z][a-z0-9_.-]{0,95}")
_SLOT = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,127}")
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_RESERVED = {"con", "prn", "aux", "nul", *("com"+str(i) for i in range(1, 10)), *("lpt"+str(i) for i in range(1, 10))}
_ASSETS = {".js", ".mjs", ".css", ".png", ".webp", ".jpg", ".jpeg", ".woff2"}


class UiPluginError(ValueError):
    def __init__(self, message: str, *, status=400):
        super().__init__(message)
        self.status = status


def _object(value, allowed: set[str], required: set[str], label: str):
    if not isinstance(value, dict) or set(value)-allowed or not required <= value.keys():
        raise UiPluginError("Invalid " + label + " fields")


def _identifier(value, *, slot=False):
    if not isinstance(value, str) or not (_SLOT if slot else _ID).fullmatch(value):
        raise UiPluginError("Invalid UI " + ("slot" if slot else "package") + " identifier")
    return value


def _version(value):
    if not isinstance(value, str) or len(value) > 64 or not _VERSION.fullmatch(value):
        raise UiPluginError("UI versions must be explicit stable semver values")
    return value


def _digest(value):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise UiPluginError("UI artifact digest must be a lowercase SHA256")
    return value


def member_name(value: str):
    if (not isinstance(value, str) or not value or len(value) > 240 or "\\" in value
            or value.startswith("/") or any(not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part)
                or part.endswith(".") or part.split(".")[0].lower() in _RESERVED
                for part in value.split("/"))):
        raise UiPluginError("Unsafe UI artifact path")
    return value


def validate_manifest(value: Any, *, default=False) -> dict:
    fields = {"schemaVersion", "id", "name", "description", "version", "sdkVersion", "provides", "requires", "entrypoint", "contributes"}
    if default:
        fields.add("buildDigest")
    _object(value, fields, {"schemaVersion", "id", "version", "sdkVersion", "provides", "requires"}, "UI manifest")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1 or value["sdkVersion"] != SDK_VERSION:
        raise UiPluginError("Unsupported UI schema or SDK version")
    identifier = _identifier(value["id"])
    if (identifier == DEFAULT_ID) != default:
        raise UiPluginError("Default UI identity cannot be supplied by an imported package")
    _version(value["version"])
    if "buildDigest" in value:
        _digest(value["buildDigest"])
    for key, maximum in (("name", 100), ("description", 2000)):
        if key in value and (not isinstance(value[key], str) or len(value[key]) > maximum
                             or any(ord(c) < 32 and c not in "\n\t" for c in value[key])):
            raise UiPluginError("Invalid UI package presentation metadata")
    for collection in ("provides", "requires"):
        entries = value[collection]
        if not isinstance(entries, list) or len(entries) > 512 or (collection == "provides" and not entries):
            raise UiPluginError("Invalid UI contract declarations")
        names = set()
        for entry in entries:
            allowed, required = ({"slot", "version", "kind", "methods"}, {"slot", "version"}) if collection == "provides" else ({"slot", "range", "optional"}, {"slot", "range"})
            _object(entry, allowed, required, "UI contract")
            slot = _identifier(entry["slot"], slot=True)
            if slot in names:
                raise UiPluginError("Duplicate UI contract declaration")
            names.add(slot)
            if collection == "provides":
                if _version(entry["version"]) != SDK_VERSION and slot.startswith(("ui.", "component.", "page.", "layout.")):
                    raise UiPluginError("Unsupported UI contract version")
                kind = entry.get("kind", "component")
                if not isinstance(kind, str) or kind not in {"component", "service"}:
                    raise UiPluginError("Unsupported UI contract kind")
                if kind == "service":
                    methods = entry.get("methods")
                    if (not slot.startswith(identifier+".") or not isinstance(methods, list) or not 1 <= len(methods) <= 64
                            or any(not isinstance(method, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", method) or method in {"constructor", "prototype", "__proto__"} for method in methods)
                            or len(set(methods)) != len(methods)):
                        raise UiPluginError("Custom UI service contracts require namespaced slots and explicit unique methods")
                elif "methods" in entry:
                    raise UiPluginError("Component UI contracts cannot declare service methods")
            else:
                if not isinstance(entry["range"], str) or len(entry["range"]) > 65 or not re.fullmatch(r"[~^]?(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", entry["range"]):
                    raise UiPluginError("Unsupported UI dependency range")
                if "optional" in entry and type(entry["optional"]) is not bool:
                    raise UiPluginError("UI optional dependency flag must be boolean")
    validate_contributions(value, default=default)
    if not default:
        entry = member_name(value.get("entrypoint", ""))
        if "/" in entry or PurePosixPath(entry).suffix not in {".js", ".mjs"}:
            raise UiPluginError("UI entrypoint must be a top-level self-contained JavaScript module")
    elif "entrypoint" in value:
        raise UiPluginError("Default UI implementation is supplied by the host Web artifact")
    return json_document(canonical_json(value, maximum=MAX_STATE_BYTES), maximum=MAX_STATE_BYTES)



def compatible(actual: str, required: str) -> bool:
    if actual == required:
        return True
    if not required or required[0] not in "^~":
        return False
    a, b = tuple(map(int, actual.split("."))), tuple(map(int, required[1:].split(".")))
    if a < b or a[0] != b[0]:
        return False
    if required[0] == "~" or b[0] == 0 and b[1] > 0:
        return a[1] == b[1]
    return a == b if b[0] == 0 else True


def validate_contributions(manifest: dict, *, default=False):
    if "contributes" not in manifest:
        return
    contributions = manifest["contributes"]
    _object(contributions, {"pages"}, set(), "UI contributions")
    pages = contributions.get("pages", [])
    if not isinstance(pages, list) or len(pages) > 64:
        raise UiPluginError("Invalid UI page contributions")
    slots = {item["slot"] for item in manifest["provides"]}
    identities, routes = set(), set()
    for page in pages:
        _object(page, {"id", "slot", "path", "title", "roles", "section", "order"},
                {"id", "slot", "path", "title", "roles"}, "UI page")
        identity = _identifier(page["id"], slot=True)
        slot, route = _identifier(page["slot"], slot=True), page["path"]
        if (not identity.startswith(manifest["id"]+".") or slot not in slots
                or next((item.get("kind", "component") for item in manifest["provides"] if item["slot"] == slot), None) != "component"
                or next((item["version"] for item in manifest["provides"] if item["slot"] == slot), None) != SDK_VERSION
                or not default and not slot.startswith(manifest["id"]+".")):
            raise UiPluginError("UI page identity and slot must belong to its package namespace")
        if not isinstance(route, str) or (not default and not re.fullmatch(
                "extensions/"+re.escape(manifest["id"])+r"/[a-z][a-z0-9-]{0,47}", route)):
            raise UiPluginError("UI page route must use its reserved extension namespace")
        if default and route not in {"overview", "services", "templates", "nodes", "images", "gateways", "logs", "features", "audit", "systemlogs", "accounts", "maintenance", "settings"}:
            raise UiPluginError("Invalid reference UI page route")
        if identity in identities or route in routes:
            raise UiPluginError("Duplicate UI page identity or route")
        identities.add(identity)
        routes.add(route)
        roles = page["roles"]
        if (not isinstance(roles, list) or not 1 <= len(roles) <= 3
                or any(not isinstance(role, str) or role not in {"admin", "operator", "viewer"} for role in roles)
                or len(set(roles)) != len(roles)):
            raise UiPluginError("UI page roles must explicitly list unique supported account roles")
        for key, maximum in (("title", 100), ("section", 60)):
            if key not in page and key == "section":
                continue
            if not isinstance(page[key], str) or not page[key].strip() or len(page[key]) > maximum or any(ord(c) < 32 for c in page[key]):
                raise UiPluginError("Invalid UI page presentation metadata")
        if "order" in page and (type(page["order"]) is not int or not -10000 <= page["order"] <= 10000):
            raise UiPluginError("Invalid UI page navigation order")

def inspect_archive(raw: bytes) -> tuple[dict, dict[str, bytes]]:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_ARCHIVE_BYTES:
        raise UiPluginError("UI artifact must be at most 8 MiB")
    files, seen, expanded = {}, set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if not 1 <= len(archive.infolist()) <= MAX_FILES:
                raise UiPluginError("UI artifact has too many members")
            for entry in archive.infolist():
                directory = entry.is_dir()
                name = member_name(entry.filename[:-1] if directory else entry.filename)
                if name.casefold() in seen or entry.orig_filename != entry.filename:
                    raise UiPluginError("Duplicate or normalized UI artifact path")
                seen.add(name.casefold())
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if kind not in ({0, stat.S_IFDIR} if directory else {0, stat.S_IFREG}) or entry.flag_bits & 1 or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise UiPluginError("UI artifact contains a link, special file or unsupported compression")
                if directory:
                    if entry.file_size:
                        raise UiPluginError("UI artifact directory contains data")
                    continue
                if name not in {"manifest.json", "README.md", "LICENSE", "NOTICE"} and PurePosixPath(name).suffix not in _ASSETS:
                    raise UiPluginError("Unsupported UI asset type")
                if not 0 <= entry.file_size <= MAX_ENTRY_BYTES:
                    raise UiPluginError("UI artifact member exceeds 1 MiB")
                expanded += entry.file_size
                if expanded > MAX_EXPANDED_BYTES or (entry.file_size > 65536 and entry.file_size > max(entry.compress_size, 1) * 256):
                    raise UiPluginError("UI artifact exceeds expansion budget")
                with archive.open(entry) as stream:
                    data = stream.read(MAX_ENTRY_BYTES+1)
                if len(data) != entry.file_size or len(data) > MAX_ENTRY_BYTES:
                    raise UiPluginError("Inconsistent UI artifact member length")
                files[name] = data
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError) as exc:
        raise UiPluginError("Invalid UI ZIP artifact") from exc
    if not {"manifest.json", "README.md", "LICENSE"} <= files.keys() or not files["LICENSE"].strip() or not files["README.md"].strip():
        raise UiPluginError("UI package requires manifest.json, nonempty README.md and LICENSE")
    manifest = validate_manifest(json_document(files["manifest.json"]))
    entry = manifest["entrypoint"]
    if entry not in files:
        raise UiPluginError("Declared UI entrypoint is missing")
    try:
        files[entry].decode("utf-8")
    except UnicodeError as exc:
        raise UiPluginError("UI entrypoint is not UTF-8") from exc
    folded_files = {name.casefold() for name in files}
    for name in files:
        if any(str(parent).casefold() in folded_files for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise UiPluginError("Ambiguous UI file/directory paths")
    return manifest, files


def default_manifest() -> dict:
    configured = os.getenv("MOTOR_UI_DEFAULT_MANIFEST_FILE", "")
    source = Path(configured) if configured else Path(__file__).resolve().parents[1] / "frontend/public/ui-default-manifest.json"
    if not configured and not source.exists():
        source = Path("/app/ui-default-manifest.json")
    return validate_manifest(json_document(read_regular(source, MAX_STATE_BYTES), maximum=MAX_STATE_BYTES), default=True)


@dataclass(frozen=True)
class UiGraph:
    bootstrap: dict
    assets: dict[tuple[str, str], bytes]


class UiPackageStore:
    def __init__(self, directory: Path, default: dict):
        self.root = Path(directory).absolute() / "ui-plugins"
        self.default = validate_manifest(default, default=True)
        ensure_directory(self.root)
        ensure_directory(self.root / "artifacts")

    def _state(self):
        path = self.root / "selection.json"
        if not path.exists() and not path.is_symlink():
            return {"schemaVersion": 1, "revision": 0, "packages": {}, "bindings": {}}
        value = json_document(read_regular(path, MAX_STATE_BYTES), maximum=MAX_STATE_BYTES)
        _object(value, {"schemaVersion", "revision", "packages", "bindings", "inherit_defaults"}, {"schemaVersion", "revision", "packages", "bindings"}, "UI selection")
        if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1 or type(value["revision"]) is not int or not 0 <= value["revision"] < 9007199254740991:
            raise UiPluginError("Invalid UI selection revision")
        if type(value.get("inherit_defaults", True)) is not bool:
            raise UiPluginError("UI inherit_defaults must be a boolean")
        return value

    def _artifact(self, digest):
        digest = _digest(digest)
        raw = read_regular(self.root / "artifacts" / (digest+".mcui"), MAX_ARCHIVE_BYTES)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise UiPluginError("UI artifact no longer matches its selected digest", status=409)
        return inspect_archive(raw)

    def import_package(self, raw: bytes):
        manifest, _ = inspect_archive(raw)
        digest = hashlib.sha256(raw).hexdigest()
        with store_lock(self.root):
            dest = self.root / "artifacts" / (digest+".mcui")
            if dest.exists():
                self._artifact(digest)
            else:
                if len(list((self.root / "artifacts").glob("*.mcui"))) >= MAX_PACKAGES:
                    raise UiPluginError("UI artifact inventory is full")
                publish_new(dest, raw)
        return {"digest": digest, "manifest": manifest, "import_executes_code": False, "activation": "next-start"}

    def graph(self, *, state=None):
        if state is None:
            with store_lock(self.root):
                return self.graph(state=self._state())
        selected, overrides = state["packages"], state["bindings"]
        inherit_defaults = state.get("inherit_defaults", True)
        if type(inherit_defaults) is not bool:
            raise UiPluginError("UI inherit_defaults must be a boolean")
        if not isinstance(selected, dict) or len(selected) > MAX_SELECTED or not isinstance(overrides, dict) or len(overrides) > 512:
            raise UiPluginError("Invalid UI package selection")
        packages, assets = {DEFAULT_ID: self.default}, {}
        entry_metadata = {}
        asset_metadata = {}
        asset_bytes = 0
        for identifier, digest in selected.items():
            _identifier(identifier)
            manifest, files = self._artifact(digest)
            if manifest["id"] != identifier:
                raise UiPluginError("UI package ID does not match selected artifact")
            packages[identifier] = manifest
            asset_metadata[identifier] = []
            for member, data in files.items():
                if PurePosixPath(member).suffix in _ASSETS:
                    asset_bytes += len(data)
                    if asset_bytes > MAX_EXPANDED_BYTES:
                        raise UiPluginError("Active UI assets exceed the 16 MiB process budget")
                    assets[(digest, member)] = data
                    asset_metadata[identifier].append({"path": member, "sha256": hashlib.sha256(data).hexdigest()})
            entry = manifest["entrypoint"]
            entry_metadata[identifier] = {"path": "/api/ui-plugins/assets/"+digest+"/"+entry, "sha256": hashlib.sha256(files[entry]).hexdigest()}
        builtin_slots = {item["slot"] for item in self.default["provides"]}
        for identifier, manifest in packages.items():
            if identifier != DEFAULT_ID:
                for contribution in manifest["provides"]:
                    if contribution["slot"] not in builtin_slots and not contribution["slot"].startswith(identifier+"."):
                        raise UiPluginError("New UI contracts must belong to their package namespace")
        bindings = {p["slot"]: DEFAULT_ID for p in self.default["provides"]} if inherit_defaults else {}
        for slot, identifier in overrides.items():
            bindings[_identifier(slot, slot=True)] = _identifier(identifier)
        for slot, identifier in bindings.items():
            if identifier not in packages or slot not in {p["slot"] for p in packages[identifier]["provides"]}:
                raise UiPluginError("Selected UI provider does not implement slot: " + slot)
        needed = set(bindings.values())
        # A standalone composition must be complete; it never silently loads reference code.
        if not inherit_defaults:
            missing = UI_KERNEL_SLOTS - bindings.keys()
            if missing:
                raise UiPluginError("Required UI kernel slot is unbound: " + sorted(missing)[0])
            # The reference bundle renders its own component catalogue. Explicit reuse
            # therefore requires that catalogue, but it is not a kernel-wide dependency.
            if DEFAULT_ID in needed and builtin_slots - bindings.keys():
                raise UiPluginError("Selected reference UI requires its complete slot bindings; inherit defaults or supply them explicitly")
        unused = set(selected)-needed
        if unused:
            raise UiPluginError("Selected UI package has no bound slots: " + sorted(unused)[0])
        page_ids, page_routes = set(), set()
        for identifier in needed:
            for page in packages[identifier].get("contributes", {}).get("pages", []):
                if identifier != DEFAULT_ID and bindings.get(page["slot"]) != identifier:
                    continue
                if page["id"] in page_ids or page["path"] in page_routes:
                    raise UiPluginError("UI page identity or route conflicts with another selected provider")
                page_ids.add(page["id"])
                page_routes.add(page["path"])
        dependencies = {identifier: set() for identifier in needed}
        for identifier in needed:
            for requirement in packages[identifier]["requires"]:
                provider = bindings.get(requirement["slot"])
                if provider is None:
                    if requirement.get("optional", False):
                        continue
                    raise UiPluginError("Missing required UI slot: " + requirement["slot"])
                contribution = next(item for item in packages[provider]["provides"] if item["slot"] == requirement["slot"])
                if not compatible(contribution["version"], requirement["range"]):
                    raise UiPluginError("UI dependency version mismatch: " + identifier + "/" + requirement["slot"])
                if provider != identifier:
                    dependencies[identifier].add(provider)
        order, visiting, visited = [], set(), set()
        def visit(identifier):
            if identifier in visiting:
                raise UiPluginError("UI provider dependencies contain a cycle")
            if identifier in visited:
                return
            visiting.add(identifier)
            for dependency in sorted(dependencies[identifier]):
                visit(dependency)
            visiting.remove(identifier)
            visited.add(identifier)
            order.append(identifier)
        for identifier in sorted(needed):
            visit(identifier)
        public = []
        for identifier in order:
            manifest = packages[identifier]
            item = {key: manifest[key] for key in ("id", "version", "sdkVersion", "provides", "requires")}
            if "contributes" in manifest:
                item["contributes"] = manifest["contributes"]
            if identifier == DEFAULT_ID and "buildDigest" in manifest:
                item["buildDigest"] = manifest["buildDigest"]
            if identifier in entry_metadata:
                item["entry"] = entry_metadata[identifier]
                item["assets"] = sorted(asset_metadata[identifier], key=lambda asset: asset["path"])
            public.append(item)
        value = {"schemaVersion": 1, "sdkVersion": SDK_VERSION, "packages": public, "bindings": bindings}
        if DEFAULT_ID in needed and "buildDigest" in self.default:
            value["buildDigest"] = self.default["buildDigest"]
        value["generation"] = hashlib.sha256(canonical_json(value, maximum=MAX_STATE_BYTES)).hexdigest()
        return UiGraph(value, assets)

    def select(self, *, packages: dict, bindings: dict, expected_revision: int, trust_code: bool, inherit_defaults: bool = True):
        if type(expected_revision) is not int or not 0 <= expected_revision < 9007199254740990 or type(trust_code) is not bool:
            raise UiPluginError("Invalid UI selection preconditions")
        if type(inherit_defaults) is not bool:
            raise UiPluginError("UI inherit_defaults must be a boolean")
        if packages and trust_code is not True:
            raise UiPluginError("Explicit trust is required for selected UI code")
        with store_lock(self.root):
            current = self._state()
            if current["revision"] != expected_revision:
                raise UiPluginError("UI selection changed; reload before saving", status=409)
            candidate = {"schemaVersion": 1, "revision": expected_revision+1, "packages": packages, "bindings": bindings}
            if not inherit_defaults:
                candidate["inherit_defaults"] = False
            graph = self.graph(state=candidate)
            previous = canonical_json(current, maximum=MAX_STATE_BYTES)
            atomic_write(self.root / "previous-selection.json", previous)
            atomic_write(self.root / "selection.json", canonical_json(candidate, maximum=MAX_STATE_BYTES))
            return {"revision": candidate["revision"], "desired_generation": graph.bootstrap["generation"], "activation": "next-start"}

    def inventory(self, active_generation: str):
        with store_lock(self.root):
            state = self._state()
            desired = self.graph(state=state)
            items = []
            paths = sorted((self.root / "artifacts").glob("*.mcui"))
            if len(paths) > MAX_PACKAGES:
                raise UiPluginError("UI artifact inventory exceeds capacity")
            for path in paths:
                manifest, _ = self._artifact(path.stem)
                items.append({"digest": path.stem, "manifest": manifest, "selected": state["packages"].get(manifest["id"]) == path.stem})
            return {"schemaVersion": 1, "revision": state["revision"], "items": items, "selection": {"packages": state["packages"], "bindings": state["bindings"], "inherit_defaults": state.get("inherit_defaults", True)}, "active_generation": active_generation,
                    "desired_generation": desired.bootstrap["generation"], "pending_restart": desired.bootstrap["generation"] != active_generation,
                    "activation": "next-start", "import_executes_code": False}
