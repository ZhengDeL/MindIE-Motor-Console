"""UID-bound role-stop plans for the native InferOperator 26.2 contract.

InferServiceSet root replicas must remain positive. Each role is an
InstanceSetSpec: its outer replicas controls workload objects, while its inner
spec.replicas belongs to the original workload template and is left untouched.
See Ascend/mind-cluster release_v26.2.0 inferserviceset_controller.go:129-139,
instanceset_controller.go:248-260 and deployment_handler.go:279-316.
StatefulSet workloads use the same index-limit deletion rule (256-292):
https://github.com/Ascend/mind-cluster/blob/release_v26.2.0/component/infer-operator/pkg/controller/workload/statefulset_handler.go#L256
"""
from __future__ import annotations

import copy
import hashlib
import json

from fastapi import HTTPException

BASELINES = "iss_role_stop_baselines"


def _reject(message):
    raise HTTPException(409, message)


def _replicas(value, *, positive=False):
    if type(value) is not int or not (1 if positive else 0) <= value <= 4096:
        _reject("Native role-stop requires bounded, explicit integer replica counts")
    return value


def _roles(document):
    roles = document.get("spec", {}).get("template", {}).get("roles")
    if not isinstance(roles, list) or not 1 <= len(roles) <= 64:
        _reject("Native role-stop requires an unambiguous InferServiceSet role layout")
    names, values, shape = set(), [], copy.deepcopy(roles)
    for index, role in enumerate(roles):
        if not isinstance(role, dict) or not isinstance(role.get("name"), str) or not role["name"] or len(role["name"]) > 253 or role["name"] in names:
            _reject("Native role-stop rejects missing or duplicate role identities")
        names.add(role["name"])
        values.append({"index": index, "name": role["name"], "replicas": _replicas(role.get("replicas"))})
        shape[index].pop("replicas")
    try:
        digest = hashlib.sha256(json.dumps(shape, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise HTTPException(409, "Native role template cannot be bound to a stop baseline") from exc
    return roles, values, digest


def plan_role_stop(state, reference, document):
    """Return an immutable baseline and CAS patch; perform no writes or I/O.

    A historical root=0 is repairable only when this UID already has a durable
    positive saved_replicas baseline from the previous stop implementation.
    The caller must persist the returned baseline before applying the patch.
    """
    metadata = document.get("metadata", {})
    identifier = reference.get("uid")
    if (reference.get("kind") != "InferServiceSet" or reference.get("apiVersion") != "mindcluster.huawei.com/v1"
            or not identifier or metadata.get("uid") != identifier
            or reference.get("namespace") != state.get("namespace")
            or metadata.get("namespace") != state.get("namespace")
            or metadata.get("name") != reference.get("name")
            or not state.get("namespace_uid") or not state.get("service_id")
            or not isinstance(metadata.get("resourceVersion"), str) or not metadata["resourceVersion"]):
        _reject("Native role-stop requires the verified resource UID, namespace and resourceVersion")
    roles, current, digest = _roles(document)
    baselines = state.get(BASELINES, {})
    if not isinstance(baselines, dict):
        _reject("Native role-stop baseline journal is malformed")
    baseline = copy.deepcopy(baselines.get(identifier))
    root = document.get("spec", {}).get("replicas", 1)
    normalize_nil = root is None
    if root is None:
        root = 1  # The native controller uses one only for a nil root value.
    _replicas(root)
    legacy = root == 0
    saved = state.get("saved_replicas", {})
    if not isinstance(saved, dict):
        _reject("Native stop replica journal is malformed")
    if legacy:
        if state.get("status") not in {"stopping", "stopped"} or identifier not in saved:
            _reject("Zero InferServiceSet replicas cannot be repaired without a UID-bound durable stop baseline")
        positive = _replicas(saved[identifier], positive=True)
    else:
        positive = _replicas(root, positive=True)
    if baseline is None:
        baseline = {"schema": 1, "service_id": state["service_id"], "namespace_uid": state["namespace_uid"],
                    "reference": copy.deepcopy(reference), "set_replicas": positive,
                    "roles": current, "role_shape_sha256": digest}
    else:
        if (not isinstance(baseline, dict) or baseline.get("schema") != 1
                or baseline.get("service_id") != state["service_id"]
                or baseline.get("namespace_uid") != state["namespace_uid"]
                or baseline.get("reference") != reference or baseline.get("role_shape_sha256") != digest
                or not isinstance(baseline.get("roles"), list)):
            _reject("Native role-stop resource or original role template differs from its durable baseline")
        positive = _replicas(baseline.get("set_replicas"), positive=True)
        if root not in {0, positive} or legacy and saved.get(identifier) != positive:
            _reject("Native InferServiceSet count changed outside its durable stop baseline")
        original = baseline["roles"]
        if len(original) != len(current) or any(not isinstance(item, dict) or item.get("index") != now["index"]
                or item.get("name") != now["name"] for item, now in zip(original, current)):
            _reject("Native role identities changed during stop")
        original_counts = [_replicas(item.get("replicas")) for item in original]
        if [item["replicas"] for item in current] not in [original_counts, [0] * len(current)]:
            _reject("Native role counts drifted outside the original or fully stopped layout")
    operations = []
    if root != positive or normalize_nil or any(item["replicas"] for item in current):
        operations.extend([{"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]},
                           {"op": "test", "path": "/spec/template/roles", "value": copy.deepcopy(roles)}])
        if "replicas" in document.get("spec", {}):
            operations.append({"op": "test", "path": "/spec/replicas", "value": document["spec"]["replicas"]})
        if root != positive or normalize_nil:
            operations.append({"op": "add", "path": "/spec/replicas", "value": positive})
        for item in current:
            if item["replicas"]:
                operations.append({"op": "replace", "path": f"/spec/template/roles/{item['index']}/replicas", "value": 0})
    return {"baseline": baseline, "operations": operations, "legacy_zero_set_repair": legacy}


def verify_role_stopped(plan, document):
    """Verify the API's patch response without claiming that Pods are gone."""
    baseline = plan["baseline"]
    reference = baseline["reference"]
    roles, counts, digest = _roles(document)
    if (document.get("metadata", {}).get("uid") != reference["uid"]
            or document.get("metadata", {}).get("namespace") != reference["namespace"]
            or document.get("spec", {}).get("replicas", 1) != baseline["set_replicas"]
            or digest != baseline["role_shape_sha256"] or any(item["replicas"] for item in counts)):
        _reject("Native role-stop response did not preserve its original role template and zero-role target")
