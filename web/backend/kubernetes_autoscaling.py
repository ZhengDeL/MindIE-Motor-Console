"""Fail-closed lifecycle for explicitly tracked, standalone Kubernetes HPAs.

Call these helpers under the runtime's existing per-service lock. They use the
runtime's authenticated kubectl transport and ownership journal, never discover
ownership from a namespace or labels alone. Native Infer Operator scalingPolicy
cannot be paused by this module: the pinned Motor checkout documents no suspend
contract and does not contain the operator controller implementation.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import re
from typing import Any

from fastapi import HTTPException


HPA = "HorizontalPodAutoscaler"
MANAGED = "motor-console.managed"
SERVICE = "motor-console.service"
TARGETS = {"Deployment", "StatefulSet", "InferServiceSet", "InferService", "InstanceSet"}
_NAME = re.compile(r"[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?\Z")
_PENDING = {"pausing", "paused", "restoring"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fail(message: str) -> None:
    raise HTTPException(409, message)


def _identity(ref: dict) -> tuple:
    return ref.get("apiVersion"), ref.get("kind"), ref.get("namespace"), ref.get("name")


def _reference(doc: dict) -> dict:
    meta = doc.get("metadata", {})
    return {"apiVersion": doc.get("apiVersion"), "kind": doc.get("kind"),
            "name": meta.get("name"), "namespace": meta.get("namespace"), "uid": meta.get("uid")}


def _entries(state: dict) -> list[dict]:
    entries = state.get("paused_hpas", [])
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        _fail("The HPA ownership journal is malformed; manual review is required")
    return entries


def _latest(state: dict) -> dict[tuple, dict]:
    return {_identity(e.get("reference", {})): e for e in _entries(state)}


def hpa_is_paused(state: dict, ref: dict) -> bool:
    """Whether an HPA has a pending/paused journal entry for this exact old UID."""
    entry = _latest(state).get(_identity(ref))
    return bool(entry and entry.get("phase") in _PENDING
                and entry.get("reference", {}).get("uid") == ref.get("uid"))


def _native_policy(document: dict) -> bool:
    if document.get("kind") != "InferServiceSet":
        return False
    roles = document.get("spec", {}).get("template", {}).get("roles", [])
    if not isinstance(roles, list):
        _fail("InferServiceSet roles are malformed; autoscaling safety cannot be verified")
    return any(isinstance(role, dict) and role.get("scalingPolicy") for role in roles)


def _verify_all(runtime: Any, state: dict) -> list[tuple[dict, dict]]:
    service_id = state.get("service_id")
    if not isinstance(service_id, str) or not service_id:
        _fail("An existing service ownership journal is required")
    expected = "motor-console-" + hashlib.sha256(service_id.encode()).hexdigest()[:24]
    if state.get("namespace") != expected or not state.get("namespace_uid"):
        _fail("The HPA namespace does not match the recorded service")
    runtime._verify_namespace(state)
    refs = state.get("resources", [])
    if not isinstance(refs, list) or len({_identity(r) for r in refs}) != len(refs):
        _fail("The resource ownership journal is malformed or contains duplicates")
    resources = []
    for ref in refs:
        if not ref.get("uid"):
            _fail("Every managed resource requires a recorded UID")
        if ref.get("namespace") not in {None, state["namespace"]}:
            _fail("The resource journal contains an object outside this service namespace")
        document = runtime._get(ref, missing=True)
        if document:
            if _identity(_reference(document)) != _identity(ref):
                _fail("The returned resource identity does not match its ownership journal")
            runtime._owned(document, service_id, ref["uid"])
            resources.append((ref, document))
    return resources


def _validate_hpa(runtime: Any, state: dict, document: dict,
                  resources: list[tuple[dict, dict]]) -> None:
    ref = _reference(document)
    if (ref["kind"] != HPA or ref["apiVersion"] not in {"autoscaling/v1", "autoscaling/v2"}
            or ref["namespace"] != state["namespace"] or not isinstance(ref["name"], str)
            or not _NAME.fullmatch(ref["name"])):
        _fail("Only an explicitly named HPA in this service's owned namespace can be managed")
    metadata = document.get("metadata", {})
    labels = metadata.get("labels", {})
    key = hashlib.sha256(state["service_id"].encode()).hexdigest()[:24]
    if labels.get(MANAGED) != "true" or labels.get(SERVICE) != key:
        _fail("HPA ownership labels do not match this service")
    if metadata.get("ownerReferences"):
        _fail("Operator/controller-owned HPA cannot be paused by deleting its child resource")
    target = document.get("spec", {}).get("scaleTargetRef", {})
    if not isinstance(target, dict) or target.get("kind") not in TARGETS:
        _fail("HPA scaleTargetRef must identify a tracked, replica-scalable service workload")
    wanted = (target.get("apiVersion"), target.get("kind"), state["namespace"], target.get("name"))
    matches = [(r, d) for r, d in resources if _identity(r) == wanted]
    if len(matches) != 1:
        _fail("HPA target is missing or is not owned and UID-tracked by this service")
    target_ref, target_doc = matches[0]
    runtime._owned(target_doc, state["service_id"], target_ref["uid"])
    if target_doc.get("metadata", {}).get("deletionTimestamp"):
        _fail("HPA target has pending deletion; refusing autoscaling changes")


def _verify_hpa_inventory(runtime: Any, state: dict) -> None:
    result = runtime._kubectl(["get", "horizontalpodautoscalers", "--namespace", state["namespace"], "-o", "json"])
    items = result.get("items")
    if not isinstance(items, list):
        _fail("The namespace HPA inventory is unavailable or malformed")
    # The API server may return v2 for an HPA originally created through v1.
    # A conversion changes neither namespace/name nor the required UID.
    refs = {(r.get("namespace"), r.get("name")): r for r in state["resources"] if r.get("kind") == HPA}
    for document in items:
        metadata = document.get("metadata", {})
        ref = refs.get((metadata.get("namespace"), metadata.get("name")))
        if not ref:
            _fail("An untracked HPA exists in the service namespace; it will not be adopted or deleted")
        runtime._owned(document, state["service_id"], ref["uid"])


def _manifest(document: dict) -> dict:
    """Keep user spec/metadata; remove server-assigned fields before create."""
    meta = document["metadata"]
    kept = {k: copy.deepcopy(meta[k]) for k in ("name", "namespace", "labels", "annotations", "finalizers") if k in meta}
    return {"apiVersion": document["apiVersion"], "kind": HPA, "metadata": kept,
            "spec": copy.deepcopy(document["spec"])}


def _contains(actual: Any, expected: Any) -> bool:
    """Compare requested fields while allowing API-server-added defaults."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _contains(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _contains(a, e) for a, e in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def _summary(state: dict, status: str) -> dict:
    return {"status": status, "hpas": [
        {"name": e["reference"]["name"], "phase": e["phase"],
         "previous_uid": e["reference"]["uid"], "restored_uid": e.get("restored_uid")}
        for e in _latest(state).values()]}


def pause_managed_hpas(runtime: Any, state: dict,
                       resources: list[tuple[dict, dict]] | None = None) -> dict:
    """Journal then UID/RV-delete every tracked standalone HPA, and confirm 404.

    The supplied resource snapshot is for caller compatibility; fresh reads of
    every tracked UID are mandatory. Old HPA references remain in resources as
    evidence until an explicit deployment retires them or restore records a new
    UID. An asynchronous deletion raises 409, so the caller must not scale yet.
    """
    live = _verify_all(runtime, state)
    if any(_native_policy(d) for _, d in live):
        _fail("Native InferServiceSet scalingPolicy has no verified pause contract in the pinned Motor source; no resources were changed")
    _verify_hpa_inventory(runtime, state)
    previous = _latest(state)
    hpas = [(r, d) for r, d in live if r["kind"] == HPA]
    live_ids = {_identity(r) for r, _ in hpas}
    for ref in state["resources"]:
        if ref.get("kind") == HPA and _identity(ref) not in live_ids and not hpa_is_paused(state, ref):
            _fail("A tracked HPA disappeared without a pause journal; review or explicitly redeploy before stopping")
    for ref, document in hpas:
        _validate_hpa(runtime, state, document, live)
        if not document.get("metadata", {}).get("resourceVersion"):
            _fail("HPA deletion requires a resourceVersion precondition")
        prior = previous.get(_identity(ref))
        if prior and prior.get("phase") in _PENDING and prior.get("reference", {}).get("uid") != ref["uid"]:
            _fail("A paused HPA's tracked UID changed; refusing a new pause cycle")
    # Persist every original manifest before the first destructive request.
    for ref, document in hpas:
        prior = previous.get(_identity(ref))
        if prior and prior.get("phase") in _PENDING:
            if prior["phase"] == "pausing" and not document["metadata"].get("deletionTimestamp"):
                # HPA status updates also change RV. An explicit retry takes a
                # fresh, journalled snapshot; an in-flight change still fails the
                # API-server precondition instead of losing an unsaved spec.
                prior.setdefault("pause_observations", []).append(copy.deepcopy(document))
                prior["manifest"] = _manifest(document)
                prior["delete_resource_version"] = document["metadata"]["resourceVersion"]
            continue
        entry = {"reference": copy.deepcopy(ref), "original": copy.deepcopy(document),
                 "manifest": _manifest(document), "phase": "pausing", "pause_requested_at": _now(),
                 "delete_resource_version": document["metadata"]["resourceVersion"]}
        state.setdefault("paused_hpas", []).append(entry)
    if hpas:
        runtime._save(state)
    pending = [e for e in _latest(state).values() if e.get("phase") in _PENDING]
    for entry in pending:
        ref = entry["reference"]
        if not any(_identity(r) == _identity(ref) and r["uid"] == ref["uid"] for r in state["resources"]):
            _fail("A paused HPA no longer has its recorded resource reference")
        document = runtime._get(ref, missing=True)
        if document:
            runtime._owned(document, state["service_id"], ref["uid"])
            if entry["phase"] in {"paused", "restoring"}:
                _fail("An HPA reappeared while paused; refusing to delete an unconfirmed replacement")
            if not document.get("metadata", {}).get("deletionTimestamp"):
                # A changed spec must not be removed using an older saved spec.
                version = entry.get("delete_resource_version", entry["original"]["metadata"]["resourceVersion"])
                runtime._kubectl(["delete", "--raw",
                                  f"/apis/{ref['apiVersion']}/namespaces/{ref['namespace']}/horizontalpodautoscalers/{ref['name']}",
                                  "-f", "-"], document={"apiVersion": "v1", "kind": "DeleteOptions",
                                  "preconditions": {"uid": ref["uid"], "resourceVersion": version},
                                  "propagationPolicy": "Foreground"})
            remaining = runtime._get(ref, missing=True)
            if remaining:
                runtime._owned(remaining, state["service_id"], ref["uid"])
                entry["phase"] = "pausing"
                runtime._save(state)
                _fail("HPA deletion is still pending; workload scale-to-zero was not authorized")
        entry.update(phase="paused", paused_at=_now())
        runtime._save(state)
    return _summary(state, "paused")


def restore_managed_hpas(runtime: Any, state: dict, *, reason: str,
                         desired_documents: list[dict] | None = None) -> dict:
    """Restore only after an explicit restart/deploy; never replace/adopt by name.

    A deploy must pass its freshly reviewed/rendered documents. A paused HPA
    omitted from that desired revision is retired, preserving its old UID/spec.
    Restart reuses the saved manifest. A create response is journalled before
    another create. A crash after create but before saving the returned UID is
    deliberately fail-closed: the unknown object requires operator review.
    """
    if reason not in {"restart", "deploy"}:
        raise HTTPException(422, "HPA restoration is allowed only for an explicit restart or deploy")
    if reason == "deploy" and not isinstance(desired_documents, list):
        raise HTTPException(422, "HPA deployment restoration requires reviewed desired_documents")
    live = _verify_all(runtime, state)
    _verify_hpa_inventory(runtime, state)
    desired: dict[tuple, dict] = {}
    for document in desired_documents or []:
        if not isinstance(document, dict):
            raise HTTPException(422, "desired_documents must contain Kubernetes objects")
        if document.get("kind") == HPA:
            identity = _identity(_reference(document))
            if identity in desired:
                _fail("The desired revision contains duplicate HPA identities")
            desired[identity] = document
    operations = []
    for identity, entry in _latest(state).items():
        if entry.get("phase") == "retired":
            continue
        if entry.get("phase") not in _PENDING | {"restored"}:
            _fail("An HPA journal phase is unknown; manual review is required")
        old = entry["reference"]
        tracked = next((r for r in state["resources"] if _identity(r) == identity), None)
        if not tracked:
            _fail("The paused HPA has no tracked ownership reference")
        existing = runtime._get(old, missing=True)
        if existing:
            runtime._owned(existing, state["service_id"], tracked["uid"])
            if tracked["uid"] == old["uid"]:
                _fail("The original HPA still exists; complete its pause before restoration")
            # A generic explicit deploy may already have created and saved its UID.
            # A UID absent from the current ownership journal is never adopted.
            _validate_hpa(runtime, state, existing, live)
            if reason == "deploy" and identity not in desired:
                _fail("A desired-omitted HPA is still active; pause it before retiring it")
            expected = desired.get(identity) if reason == "deploy" else entry.get("restore_manifest", entry.get("manifest"))
            if expected:
                _validate_hpa(runtime, state, expected, live)
                if (not _contains(existing.get("spec"), expected.get("spec"))
                        or not _contains(existing["metadata"].get("labels", {}), expected["metadata"].get("labels", {}))
                        or not _contains(existing["metadata"].get("annotations", {}), expected["metadata"].get("annotations", {}))):
                    _fail("The tracked active HPA differs from the requested restoration; explicit deployment is required")
            operations.append((entry, existing, "known"))
            continue
        if entry.get("phase") == "restored" and reason == "restart":
            _fail("A previously restored HPA disappeared; explicit deployment is required")
        document = desired.get(identity) if reason == "deploy" else entry.get("restore_manifest", entry.get("manifest"))
        if document is None:
            operations.append((entry, None, "retire"))
            continue
        _validate_hpa(runtime, state, document, live)
        operations.append((entry, _manifest(document), "create"))
    # Finish every ownership/target/name-conflict check before the first create.
    for entry, document, operation in operations:
        old = entry["reference"]
        if operation == "retire":
            state["resources"] = [r for r in state["resources"] if _identity(r) != _identity(old)]
            entry.update(phase="retired", retired_at=_now())
        elif operation == "known":
            entry.update(phase="restored", restored_uid=document["metadata"]["uid"], restored_at=_now())
            entry["restore_manifest"] = _manifest(document)
        else:
            entry.update(phase="restoring", restore_requested_at=_now(), restore_reason=reason)
            runtime._save(state)
            created = runtime._kubectl(["create", "-f", "-", "-o", "json"], document=document)
            current = _reference(created)
            if _identity(current) != _identity(old) or not current.get("uid"):
                _fail("HPA create returned no verifiable identity/UID; review the namespace before retrying")
            runtime._owned(created, state["service_id"], current["uid"])
            state["resources"] = [current if _identity(r) == _identity(old) else r for r in state["resources"]]
            entry.update(phase="restored", restored_uid=current["uid"], restored_at=_now())
            entry["restore_manifest"] = copy.deepcopy(document)
        runtime._save(state)
    return _summary(state, "active")
