"""Durable receipts and whole-device claims for one execution Agent.

The database is an Agent journal, not a lock on external Docker users. Unknown
effects never expire into free capacity; they are reconciled against identities.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from .observability import redact

AGENT_APPLICATION_ID = 0x4D43414A  # MCAJ; incompatible journals are never adopted implicitly.
_SESSION = uuid.uuid4().hex
_INIT = threading.RLock()
_INITIALIZED: set[str] = set()
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_MAX_RECEIPTS = 20000


class AgentStateConflict(ValueError):
    pass


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def operation_identifier(value) -> str:
    if value is None or value == "":
        return "legacy-" + uuid.uuid4().hex
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("operation_id must be a 1..128 character simple identifier")
    return value


def intent_hash(action: str, payload: dict) -> str:
    # Only the digest is stored: configuration, credentials and environment are not.
    intent = {key: value for key, value in payload.items() if key != "operation_id"}
    return hashlib.sha256(json.dumps({"action": action, "intent": intent}, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class DockerAgentState:
    def __init__(self, directory: str | Path, *, initialization_timeout=10,
                 provider_generation=None, provider_binding=None):
        self.provider_generation, self.provider_binding = provider_generation, provider_binding
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "agent-runtime.sqlite3"
        with _INIT, self.transaction(timeout=initialization_timeout) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise AgentStateConflict("Agent operation database is newer than this build")
            db.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                       "session TEXT NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS allocations (id TEXT PRIMARY KEY, daemon_id TEXT NOT NULL, "
                       "state TEXT NOT NULL, updated_at TEXT NOT NULL, body TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS allocations_daemon ON allocations(daemon_id,state)")
            db.execute("PRAGMA application_id=" + str(AGENT_APPLICATION_ID))
            db.execute("PRAGMA user_version=1")
            if str(self.path) not in _INITIALIZED:
                for row in db.execute("SELECT id,body FROM operations WHERE state IN ('accepted','running') AND session<>?", (_SESSION,)).fetchall():
                    value = json.loads(row["body"])
                    value.update(operation_status="interrupted", finished_at=stamp(), resources_may_exist=True,
                                 error_code="agent_restarted", message="Agent restarted; observe the recorded resources before a new operation")
                    self._put_operation(db, value, row["id"])
                for row in db.execute("SELECT body FROM allocations WHERE state='pending'").fetchall():
                    value = json.loads(row["body"])
                    op = db.execute("SELECT state FROM operations WHERE id=?", (value["operation_id"],)).fetchone()
                    if op is None or op[0] == "interrupted":
                        value.update(state="uncertain", updated_at=stamp())
                        self._put_allocation(db, value)
                _INITIALIZED.add(str(self.path))
        self.path.chmod(0o600)

    @contextmanager
    def transaction(self, *, timeout=10):
        db = sqlite3.connect(self.path, timeout=timeout)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=" + str(max(0, int(timeout * 1000))))
            identity = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if identity != AGENT_APPLICATION_ID and (identity != 0 or version != 0
                    or db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()):
                raise AgentStateConflict("Agent journal belongs to another product; use an independent Agent data directory")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _put_operation(db, value, identifier, fingerprint=None):
        if fingerprint is None:
            db.execute("UPDATE operations SET state=?,updated_at=?,body=? WHERE id=?",
                       (value["operation_status"], stamp(), json.dumps(value), identifier))
        else:
            db.execute("INSERT INTO operations VALUES (?,?,?,?,?,?)",
                       (identifier, fingerprint, _SESSION, value["operation_status"], stamp(), json.dumps(value)))

    @staticmethod
    def _put_allocation(db, value):
        db.execute("INSERT INTO allocations VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "daemon_id=excluded.daemon_id,state=excluded.state,updated_at=excluded.updated_at,body=excluded.body",
                   (value["allocation_id"], value["daemon_id"], value["state"], value["updated_at"], json.dumps(value)))

    def begin(self, identifier: str, fingerprint: str, action: str, service_id: str, node_id: str,
              *, public_intent: dict | None = None):
        if public_intent is not None:
            if (action not in {"image_pull", "image_remove"} or not isinstance(public_intent, dict)
                    or set(public_intent) - {"image_reference", "image_id"}
                    or any(not isinstance(value, str) or len(value) > 512
                           or any(ord(character) < 32 for character in value) for value in public_intent.values())):
                raise AgentStateConflict("Invalid public image intent metadata")
        with self.transaction() as db:
            previous = db.execute("SELECT fingerprint,body FROM operations WHERE id=?", (identifier,)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise AgentStateConflict("operation_id is already bound to a different intent")
                return self.public_receipt(json.loads(previous["body"]), replayed=True)
            # Never expire an idempotency identity into permission to execute again.
            if db.execute("SELECT count(*) FROM operations").fetchone()[0] >= _MAX_RECEIPTS:
                raise AgentStateConflict("Agent receipt capacity reached; unresolved history must be reviewed")
            value = {"operation_id": identifier, "action": action, "service_id": service_id,
                     "node_id": node_id or "", "operation_status": "accepted", "received_at": stamp(),
                     "started_at": None, "finished_at": None, "resources_may_exist": False,
                     "result": None, "error_code": None, "message": "Operation accepted"}
            if self.provider_generation:
                value.update(provider_generation=self.provider_generation, provider_binding=self.provider_binding)
            if public_intent is not None:
                value["intent"] = dict(public_intent)
            self._put_operation(db, value, identifier, fingerprint)
        return None

    def replay(self, identifier, fingerprint):
        with self.transaction() as db:
            previous = db.execute("SELECT fingerprint,body FROM operations WHERE id=?", (identifier,)).fetchone()
            if previous is None:
                return None
            if previous["fingerprint"] != fingerprint:
                raise AgentStateConflict("operation_id is already bound to a different intent")
            return self.public_receipt(json.loads(previous["body"]), replayed=True)

    def running(self, identifier):
        with self.transaction() as db:
            value = json.loads(db.execute("SELECT body FROM operations WHERE id=?", (identifier,)).fetchone()[0])
            value.update(operation_status="running", started_at=stamp(), message="Operation executing", resources_may_exist=True)
            self._put_operation(db, value, identifier)

    def finish(self, identifier, *, result=None, error=None, resources_may_exist=False):
        with self.transaction() as db:
            value = json.loads(db.execute("SELECT body FROM operations WHERE id=?", (identifier,)).fetchone()[0])
            if error is None:
                fields = ("container_id", "status", "message", "workspace")
                if value.get("action") in {"image_pull", "image_remove"}:
                    fields = ("status", "message", "image_id", "image_reference", "repo_digests", "size_bytes")
                allowed = {key: redact(result[key]) for key in fields if key in result}
                value.update(operation_status="completed", result=allowed, message=allowed.get("message", "Operation completed"))
            else:
                value.update(operation_status="failed", error_code="operation_failed",
                             message=str(redact(str(error)))[:2000])
            value.update(finished_at=stamp(), resources_may_exist=resources_may_exist)
            self._put_operation(db, value, identifier)
            return self.public_receipt(value)

    def interrupt(self, identifier, *, message: str, resources_may_exist: bool = True):
        """Keep a durable unknown result after an execution interruption."""
        with self.transaction() as db:
            row = db.execute("SELECT body FROM operations WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise AgentStateConflict("No accepted operation exists for interruption")
            value = json.loads(row[0])
            if value["operation_status"] == "completed":
                return self.public_receipt(value)
            value.update(operation_status="interrupted", finished_at=stamp(),
                         error_code="outcome_unknown", message=str(redact(message))[:2000],
                         resources_may_exist=resources_may_exist)
            self._put_operation(db, value, identifier)
            return self.public_receipt(value)

    @staticmethod
    def public_receipt(value, *, replayed=False):
        result = value.get("result") or {}
        status = result.get("status", "completed") if value["operation_status"] == "completed" else {
            "failed": "failed", "interrupted": "unknown"}.get(value["operation_status"], "pending")
        return {**result, **value, "status": status, "replayed": replayed}

    def receipt(self, identifier):
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise ValueError("Invalid operation identifier")
        with self.transaction() as db:
            row = db.execute("SELECT body FROM operations WHERE id=?", (identifier,)).fetchone()
            return self.public_receipt(json.loads(row[0])) if row else None

    def reserve_many(self, claims: list[dict]):
        if not claims:
            return
        if len(claims) > 128:
            raise AgentStateConflict("Resource claim group exceeds its limit")
        with self.transaction() as db:
            current = [json.loads(row[0]) for row in db.execute("SELECT body FROM allocations WHERE state<>'released'")]
            replacing = {claim["allocation_id"]: claim for claim in claims}
            if len(replacing) != len(claims):
                raise AgentStateConflict("Allocation group contains duplicate identities")
            new_count = sum(db.execute("SELECT 1 FROM allocations WHERE id=?", (claim["allocation_id"],)).fetchone() is None for claim in claims)
            if db.execute("SELECT count(*) FROM allocations").fetchone()[0] + new_count > _MAX_RECEIPTS:
                raise AgentStateConflict("Agent allocation history capacity reached; review retained records")
            incoming = []
            for claim in claims:
                for other in [*current, *incoming]:
                    if other["allocation_id"] in replacing and other not in incoming:
                        replacement = replacing[other["allocation_id"]]
                        same_owner = (other["service_id"] == replacement["service_id"]
                                      and other["node_id"] == replacement["node_id"]
                                      and set(other.get("container_ids", [])) == set(replacement.get("container_ids", [])))
                        if (other["state"] != "active" or not same_owner or not other.get("container_ids")
                                or other.get("unresolved_container_names") or other.get("unresolved_restarts")):
                            raise AgentStateConflict("Unresolved allocation cannot be replaced: " + other["allocation_id"])
                        continue
                    if claim["daemon_id"] != other["daemon_id"]:
                        continue
                    overlap = set(claim["devices"]) & set(other["devices"])
                    port_overlap = set(claim["ports"]) & set(other["ports"])
                    if overlap or port_overlap:
                        raise AgentStateConflict("Resources are reserved by another allocation: " + other["allocation_id"])
                incoming.append(claim)
            for claim in claims:
                self._put_allocation(db, {**claim, "state": "pending", "updated_at": stamp(), "observed_at": None,
                                          "creation_tracking": True, "unresolved_container_names": [],
                                          "unresolved_restarts": {}})

    def creating(self, identifier, name):
        """Persist the possible side effect before invoking Docker create/start."""
        with self.transaction() as db:
            value = json.loads(db.execute("SELECT body FROM allocations WHERE id=?", (identifier,)).fetchone()[0])
            if name not in value["container_names"]:
                raise AgentStateConflict("Container creation is outside the recorded allocation")
            value["unresolved_container_names"] = sorted(set(value.get("unresolved_container_names", [])) | {name})
            value["updated_at"] = stamp()
            self._put_allocation(db, value)

    def attach(self, identifier, containers, *, names=()):
        with self.transaction() as db:
            row = db.execute("SELECT body FROM allocations WHERE id=?", (identifier,)).fetchone()
            if row:
                value = json.loads(row[0])
                value["container_ids"] = list(dict.fromkeys([*value.get("container_ids", []), *containers]))
                value["unresolved_container_names"] = sorted(set(value.get("unresolved_container_names", [])) - set(names))
                if containers and not names:
                    # Old callers did not identify which planned name was created.
                    value["unresolved_container_names"] = list(value["container_names"])
                value["updated_at"] = stamp()
                self._put_allocation(db, value)

    def restarting(self, identifier, container_id, started_at):
        with self.transaction() as db:
            value = json.loads(db.execute("SELECT body FROM allocations WHERE id=?", (identifier,)).fetchone()[0])
            value.setdefault("unresolved_restarts", {})[container_id] = str(started_at or "")[:64]
            value.update(state="pending", updated_at=stamp())
            self._put_allocation(db, value)

    def restarted(self, identifier, container_id):
        with self.transaction() as db:
            value = json.loads(db.execute("SELECT body FROM allocations WHERE id=?", (identifier,)).fetchone()[0])
            value.setdefault("unresolved_restarts", {}).pop(container_id, None)
            value["updated_at"] = stamp()
            self._put_allocation(db, value)

    def uncertain(self, operation_id):
        with self.transaction() as db:
            for row in db.execute("SELECT body FROM allocations WHERE state<>'released'").fetchall():
                value = json.loads(row[0])
                if value["operation_id"] == operation_id:
                    value.update(state="uncertain", updated_at=stamp())
                    self._put_allocation(db, value)

    def confirmed_terminal(self, daemon_id, service_id, node_id, container_id, name):
        """A successful owned stop/remove acknowledges this exact known target."""
        with self.transaction() as db:
            rows = db.execute("SELECT body FROM allocations WHERE daemon_id=? AND state<>'released'", (daemon_id,)).fetchall()
            for row in rows:
                value = json.loads(row[0])
                if value["service_id"] != service_id or value["node_id"] != node_id:
                    continue
                observations = value.get("observed_creation_ids", {})
                confirmed_names = {target for target, identifier in observations.items() if identifier == container_id}
                attached = container_id in value.get("container_ids", [])
                known = attached or bool(confirmed_names)
                if not known:
                    continue
                if attached and name not in observations:
                    confirmed_names.add(name)
                unresolved = set(value.get("unresolved_container_names", []))
                restarts = value.get("unresolved_restarts", {})
                if unresolved & confirmed_names or container_id in restarts:
                    unresolved.difference_update(confirmed_names)
                    restarts.pop(container_id, None)
                    value.update(unresolved_container_names=sorted(unresolved), unresolved_restarts=restarts, updated_at=stamp())
                    self._put_allocation(db, value)

    def inventory_claims(self, daemon_id, *, timeout=1):
        """Complete, bounded nonreleased records used to select read targets."""
        with self.transaction(timeout=timeout) as db:
            rows = db.execute("SELECT body FROM allocations WHERE daemon_id=? AND state<>'released' LIMIT 4097",
                              (daemon_id,)).fetchall()
        if len(rows) > 4096:
            raise AgentStateConflict("Current allocation references exceed the observation budget")
        values = [json.loads(row[0]) for row in rows]
        return {value["allocation_id"]: value for value in values}

    def reconcile(self, daemon_id, containers, label_keys, *, timeout=10, deadline=None, expected_claims=None):
        managed, service, node = label_keys
        by_id = {container.id: container for container in containers}
        by_name = {container.name: container for container in containers} if deadline is not None else {}
        with self.transaction(timeout=timeout) as db:
            rows = db.execute("SELECT body FROM allocations WHERE daemon_id=? AND state<>'released'", (daemon_id,)).fetchall()
            for row in rows:
                if deadline is not None and time.monotonic() >= deadline:
                    raise AgentStateConflict("Resource reconciliation exceeded its read budget")
                value = json.loads(row[0])
                if expected_claims is not None and expected_claims.get(value["allocation_id"]) != value:
                    # A filtered observation cannot prove absence for a claim
                    # created/changed after its target selection snapshot.
                    continue
                observations = value.setdefault("observed_creation_ids", {})
                ids = list(dict.fromkeys([*value.get("container_ids", []),
                    *value.get("unresolved_restarts", {}), *observations.values()]))
                names = set(value.get("container_names", [])) | set(value.get("unresolved_container_names", []))
                found = []
                candidates = containers
                if deadline is not None:
                    matched = [by_id[item] for item in ids if item in by_id]
                    matched.extend(by_name[item] for item in names if item in by_name)
                    candidates = list({item.id: item for item in matched}.values())
                for container in candidates:
                    labels = (container.attrs.get("Config") or {}).get("Labels") or {}
                    exact = container.id in ids
                    discovered = (container.name in names
                                  and (container.name not in observations or observations[container.name] == container.id)
                                  and labels.get("motor-console.operation-id") == value["operation_id"])
                    if (exact or discovered) and labels.get(managed) == "true" and labels.get(service) == value["service_id"] and labels.get(node) == value["node_id"]:
                        found.append(container)
                op = db.execute("SELECT state FROM operations WHERE id=?", (value["operation_id"],)).fetchone()
                states = {getattr(container, "status", "unknown") for container in found}
                unresolved = set(value.get("unresolved_container_names", value.get("container_names", [])))
                for container in found:
                    if container.name in unresolved:
                        # A name discovers an identity once; it is not an
                        # acknowledgement that create/start finished.
                        observations.setdefault(container.name, container.id)
                found_by_id = {container.id: container for container in found}
                for name in list(unresolved):
                    identifier = observations.get(name)
                    container = found_by_id.get(identifier)
                    if identifier and identifier not in by_id:
                        # The complete observation proves the exact discovered
                        # target was deleted, even if its old name was reused.
                        unresolved.discard(name)
                    elif container and (container.status in {"running", "restarting", "paused"}
                                        or op and op[0] == "completed"):
                        unresolved.discard(name)
                value["unresolved_container_names"] = sorted(unresolved)
                restarts = value.setdefault("unresolved_restarts", {})
                for identifier, before in list(restarts.items()):
                    container = by_id.get(identifier)
                    after = (container.attrs.get("State") or {}).get("StartedAt", "") if container else ""
                    if container is None or (before and after and before != after
                            and getattr(container, "status", "unknown") in {"running", "restarting", "paused"}):
                        del restarts[identifier]
                if unresolved or restarts:
                    state = "pending" if op and op[0] in {"accepted", "running"} else "uncertain"
                elif states & {"running", "restarting", "paused"}:
                    state = "active"
                elif found and states <= {"exited", "dead", "created"} and not (op and op[0] in {"accepted", "running"}):
                    state = "released"
                elif ids and not any(identifier in by_id for identifier in ids) and not (op and op[0] in {"accepted", "running"}):
                    state = "released"
                elif not ids and value.get("creation_tracking") and not (op and op[0] in {"accepted", "running"}):
                    state = "released"
                else:
                    state = "pending" if op and op[0] in {"accepted", "running"} else "uncertain"
                value.update(state=state, observed_at=stamp(), updated_at=stamp())
                if found:
                    value["container_ids"] = [container.id for container in found]
                self._put_allocation(db, value)

    def allocation(self, identifier):
        with self.transaction() as db:
            row = db.execute("SELECT body FROM allocations WHERE id=?", (identifier,)).fetchone()
            return json.loads(row[0]) if row else None

    def allocations(self, daemon_id, *, timeout=10):
        with self.transaction(timeout=timeout) as db:
            current = db.execute("SELECT body FROM allocations WHERE daemon_id=? AND state<>'released' "
                                 "ORDER BY updated_at DESC LIMIT 257", (daemon_id,)).fetchall()
            remaining = max(0, 256 - len(current))
            history = db.execute("SELECT body FROM allocations WHERE daemon_id=? AND state='released' "
                                 "ORDER BY updated_at DESC LIMIT ?", (daemon_id, remaining)).fetchall()
        items = [json.loads(row[0]) for row in current[:256]] + [json.loads(row[0]) for row in history]
        # daemon_id is supplied at the envelope; claims do not leak internal paths.
        for item in items:
            item.pop("daemon_id", None)
            item.pop("creation_tracking", None)
            item.pop("unresolved_container_names", None)
            item.pop("unresolved_restarts", None)
            item.pop("observed_creation_ids", None)
        return {"schema_version": 1, "scope": "single-agent-docker-daemon", "daemon_id": daemon_id,
                "collected_at": stamp(), "complete": len(current) <= 256, "items": items}
