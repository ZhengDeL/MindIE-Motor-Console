"""Package an explicitly reviewed native checkout; this does not certify compatibility.

The operator supplies a contract-acceptance report. The tool records its hash,
pins an exact clean Git commit and refuses runtime links or an existing output
path. Documentation links can only be explicitly omitted, never followed.
No deployment, network access, native upgrade or automatic contract test occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.version import ADAPTER_DRIVER, AGENT_PROTOCOL_VERSION
from scripts.product_paths import require_product_path


def package(upstream_dir, commit, profile_id, output_dir, contract_evidence, *, omit_documentation_sources=False):
    source, evidence = (Path(p).resolve() for p in (upstream_dir, contract_evidence))
    output = require_product_path(output_dir, "Packaged native Motor profile")
    if not re.fullmatch(r"[0-9a-f]{40}", commit): raise ValueError("Pin an exact lowercase Git commit")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", profile_id): raise ValueError("Invalid profile identity")
    if not profile_id.endswith("-" + commit): raise ValueError("Profile identity must end with '-' and its full native commit")
    if output.exists() or output.is_relative_to(source): raise ValueError("Output must be a new directory outside the source checkout")
    if not evidence.is_file() or not 1 <= evidence.stat().st_size <= 10 * 1024 * 1024:
        raise ValueError("Supply the reviewed adapter contract-acceptance report (1 byte..10 MiB)")
    def git(*arguments):
        result = subprocess.run(["git", "-C", str(source), *arguments], capture_output=True, check=False)
        if result.returncode: raise ValueError("Git checkout verification failed")
        return result.stdout
    if git("rev-parse", "HEAD").decode().strip() != commit: raise ValueError("Source HEAD differs from the requested commit")
    if git("status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all").strip(): raise ValueError("Source checkout has tracked modifications")
    files, omitted_documentation_sources = {}, []
    for entry in git("ls-files", "--stage", "-z").split(b"\0"):
        if not entry: continue
        metadata, name = entry.split(b"\t", 1)
        mode, object_id, stage = metadata.split()
        relative = name.decode("utf-8")
        fragment = PurePosixPath(relative)
        if (fragment.is_absolute() or ".." in fragment.parts or "\\" in relative or ":" in relative
                or any(ord(c) < 32 for c in relative)):
            raise ValueError("Source inventory contains an unsafe path")
        path = source / relative
        if (omit_documentation_sources and relative.startswith("docs/")
                and mode in {b"120000", b"160000"} and stage == b"0"):
            # Inspect only the index entry: do not resolve/stat the target or
            # recurse into a documentation submodule, even if materialized.
            omitted_documentation_sources.append({"path": relative, "mode": mode.decode("ascii"),
                "objectid": object_id.decode("ascii"),
                "reason": "Explicit operator omission of documentation-only link; target not followed or packaged"})
            continue
        if (mode not in {b"100644", b"100755"} or stage != b"0" or path.is_symlink()
                or not path.resolve().is_relative_to(source) or not path.is_file()):
            raise ValueError("Source inventory contains a symlink, submodule or unsafe entry")
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not {"examples/deployer/docker_deploy.py", "examples/deployer/deploy.py"}.issubset(files):
        raise ValueError("Required native entry points are absent")
    manifest = {"profile_schema": 1, "agent_protocol": AGENT_PROTOCOL_VERSION, "driver": ADAPTER_DRIVER,
                "profile_id": profile_id, "commit": commit, "contract_status": "contract-tested",
                "contract_evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                "contract_evidence_kind": "operator-supplied-attestation; not verified by this packaging tool",
                "omitted_documentation_sources": omitted_documentation_sources, "files": files}
    output.mkdir(parents=True, mode=0o700)
    try:
        for relative, sha in files.items():
            target = output / "upstream" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != sha: raise ValueError("Source changed during packaging")
            target.chmod(0o555 if os.access(source / relative, os.X_OK) else 0o444)
        raw = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        (output / "manifest.json").write_bytes(raw)
        (output / "manifest.json").chmod(0o444)
    except Exception:
        # Preserve incomplete output for inspection; never recursively remove a computed path.
        raise
    return {"packaged": True, "profile_id": profile_id, "commit": commit, "verified_files": len(files),
            "manifest_sha256": hashlib.sha256(raw).hexdigest(), "output_directory": str(output),
            "omitted_documentation_sources": omitted_documentation_sources,
            "runtime_acceptance": "not verified by this packaging tool",
            "contract_acceptance": "operator-supplied; no automatic future-version certification"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("upstream-dir", "commit", "profile-id", "output-dir", "contract-evidence"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--omit-documentation-sources", action="store_true",
                        help="Explicitly omit docs/ symlink/gitlink entries, recording their identity without following targets; runtime links remain forbidden")
    args = parser.parse_args()
    try:
        print(json.dumps(package(args.upstream_dir, args.commit, args.profile_id, args.output_dir, args.contract_evidence,
                                 omit_documentation_sources=args.omit_documentation_sources), indent=2))
    except (OSError, ValueError) as exc:
        print(json.dumps({"packaged": False, "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__": raise SystemExit(main())
