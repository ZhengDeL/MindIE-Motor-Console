"""Redacted request context and bounded, indexed local JSON logs.

Field names follow the OpenTelemetry log model where applicable. This module
does not claim to emit OTLP or install an external collector/Loki service.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
from uuid import uuid4


_context: ContextVar[dict | None] = ContextVar("motor_console_request_context", default=None)
_secrets: set[str] = set()
_secrets_guard = threading.RLock()
_sensitive = re.compile(r"authorization|password|passwd|secret|token|apikey|privatekey|credential|cookie", re.I)
_assignment = re.compile(
    r"(?<![\w.-])([\"']?([A-Za-z_][\w.-]{0,127})[\"']?\s*[:=]\s*)"
    r'(?:"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^\s,;&}]+)', re.I)
_pem = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
_levels = {"TRACE": 1, "DEBUG": 5, "INFO": 9, "WARN": 13, "ERROR": 17, "FATAL": 21}


class _StrictRotatingHandler(RotatingFileHandler):
    def handleError(self, _record):
        # A disk-full/permission error must not silently produce index-only logs.
        raise OSError("Structured JSON log could not be written")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_secret(value: str) -> None:
    """Register a known credential for free-text redaction; never persist it."""
    if isinstance(value, str) and len(value) >= 4:
        with _secrets_guard:
            _secrets.add(value)


def _text(value: str) -> str:
    value = _pem.sub("[REDACTED PRIVATE KEY]", value)
    value = re.sub(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 [REDACTED]", value, flags=re.I)
    value = _assignment.sub(lambda match: match.group(1) + "[REDACTED]"
                            if _sensitive.search(re.sub(r"[^a-z0-9]", "", match.group(2).lower())) else match.group(0), value)
    value = re.sub(r"(://[^\s:/@]+):[^\s/@]+@", r"\1:[REDACTED]@", value)
    with _secrets_guard:
        for secret in sorted(_secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
    return value[:4096] + ("...[truncated]" if len(value) > 4096 else "")


def redact(value, *, _depth: int = 0):
    """Mask credential fields and common free-text encodings, with size bounds."""
    if _depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:100]:
            name = str(key)
            normalized = re.sub(r"[^a-z0-9]", "", name.lower())
            if _sensitive.search(normalized) or normalized in {"headers", "requestbody", "responsebody", "query", "querystring"}:
                result[name] = "[REDACTED]"
            else:
                result[name] = redact(item, _depth=_depth + 1)
        if len(value) > 100:
            result["_truncated"] = True
        return result
    if isinstance(value, (list, tuple)):
        result = [redact(item, _depth=_depth + 1) for item in value[:100]]
        return result + (["[TRUNCATED]"] if len(value) > 100 else [])
    if isinstance(value, str):
        # A detail may itself be serialized JSON rather than a dict.
        if value.lstrip().startswith(("{", "[")) and len(value) <= 65536:
            try:
                return json.dumps(redact(json.loads(value), _depth=_depth + 1), ensure_ascii=False)
            except (ValueError, RecursionError):
                pass
        return _text(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return _text(str(value))


def timestamp_filter(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Timestamp must be an ISO8601 string with a timezone")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Timestamp must be ISO8601") from exc
    if stamp.tzinfo is None:
        raise ValueError("Timestamp must include its timezone")
    return stamp.astimezone(timezone.utc).isoformat()


def actor_identity(actor) -> str:
    if isinstance(actor, dict):
        actor = actor.get("id") or actor.get("username") or actor.get("name") or "system"
    return _text(str(actor or "system"))[:128]


def context_fields() -> dict:
    return dict(_context.get() or {"actor": "system", "request_id": ""})


@contextmanager
def request_context(*, actor=None, request_id: str | None = None):
    current = context_fields()
    requested = request_id or current.get("request_id") or uuid4().hex
    if not isinstance(requested, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", requested):
        requested = uuid4().hex
    value = {"actor": actor_identity(actor if actor is not None else current["actor"]), "request_id": requested}
    token = _context.set(value)
    try:
        yield value
    finally:
        _context.reset(token)


for _key, _value in os.environ.items():
    if _sensitive.search(re.sub(r"[^a-z0-9]", "", _key.lower())):
        register_secret(_value)


class JsonLogStore:
    """One process writes rotated JSONL; a rebuildable SQL index serves queries.

    The bounded file window is scanned once on startup, one line at a time. Query
    and export read indexed rows, never reload all rotation files per request.
    External replacement/editing of log files requires a restart to reindex.
    """
    def __init__(self, directory: str | Path, max_bytes: int = 10 * 1024 * 1024,
                 backup_count: int = 7, service_name: str = "motor-console"):
        if type(max_bytes) is not int or not 1024 <= max_bytes <= 128 * 1024 * 1024:
            raise ValueError("Log segment size must be between 1 KiB and 128 MiB")
        if type(backup_count) is not int or not 1 <= backup_count <= 32:
            raise ValueError("Log rotation count must be between 1 and 32")
        if max_bytes * (backup_count + 1) > 512 * 1024 * 1024:
            raise ValueError("Local request log window must not exceed 512 MiB")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "requests.jsonl"
        self.index_path = self.directory / "request-index.sqlite3"
        self.max_bytes, self.backup_count = max_bytes, backup_count
        self.service_name = service_name
        self._guard = threading.RLock()
        self._closed = False
        self._handler = _StrictRotatingHandler(self.path, maxBytes=0, backupCount=backup_count, encoding="utf-8")
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self.path.chmod(0o600)
        self.segment_id = uuid4().hex
        self.reindex_skipped = 0
        try:
            self._initialize_index()
        except Exception:
            self._handler.close()
            raise

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.index_path, timeout=20)
        try:
            connection.execute("PRAGMA busy_timeout=20000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _files(self) -> list[Path]:
        return [path for path in [self.path, *(self.path.with_name(self.path.name + "." + str(index))
                                             for index in range(1, self.backup_count + 1))] if path.is_file()]

    @staticmethod
    def _insert(db, record):
        attributes = record.get("attributes") or {}
        db.execute("""INSERT OR REPLACE INTO request_logs
            (id,time,severity,actor,request_id,event,path,status_code,segment,body) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (record["id"], record["timestamp"], record["severity_text"], record.get("actor", "system"),
             record.get("request_id", ""), str(record.get("body", "")), str(attributes.get("path", "")),
             attributes.get("status_code") if type(attributes.get("status_code")) is int else None,
             record["segment_id"], json.dumps(record, ensure_ascii=False, separators=(",", ":"))))

    def _initialize_index(self):
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Request-log index is newer than this implementation")
            db.execute("""CREATE TABLE IF NOT EXISTS request_logs (
                id TEXT PRIMARY KEY, time TEXT NOT NULL, severity TEXT NOT NULL,
                actor TEXT NOT NULL, request_id TEXT NOT NULL, event TEXT NOT NULL,
                path TEXT NOT NULL, status_code INTEGER, segment TEXT NOT NULL, body TEXT NOT NULL)""")
            for field in ("time", "severity", "actor", "request_id", "segment"):
                db.execute("CREATE INDEX IF NOT EXISTS request_logs_" + field + " ON request_logs(" + field + ")")
            db.execute("PRAGMA user_version=1")
            db.execute("DELETE FROM request_logs")
            consumed = 0
            for path in reversed(self._files()):
                with path.open("rb") as source:
                    for line in source:
                        consumed += len(line)
                        if consumed > 2 * self.max_bytes * (self.backup_count + 1):
                            raise ValueError("Existing request logs exceed the configured bounded window")
                        if len(line) > 65536:
                            self.reindex_skipped += 1
                            continue
                        try:
                            record = redact(json.loads(line))
                            if not all(key in record for key in ("id", "timestamp", "severity_text", "segment_id")):
                                raise ValueError("Missing structured fields")
                            self._insert(db, record)
                            if path == self.path:
                                self.segment_id = record["segment_id"]
                        except (ValueError, TypeError, KeyError):
                            self.reindex_skipped += 1
        self.index_path.chmod(0o600)

    def _retained_segments(self) -> set[str]:
        segments = set()
        for path in self._files():
            with path.open("rb") as source:
                first = source.readline(65537)
            try:
                segments.add(json.loads(first)["segment_id"])
            except (ValueError, KeyError, TypeError):
                continue
        return segments

    def write(self, event: str, *, severity: str = "INFO", actor=None, request_id: str | None = None, **fields) -> dict:
        severity = {"WARNING": "WARN", "CRITICAL": "FATAL"}.get(severity.upper(), severity.upper())
        if severity not in _levels:
            raise ValueError("Unsupported structured log severity")
        current = context_fields()
        attributes = redact(fields)
        if isinstance(attributes.get("path"), str):
            attributes["path"] = attributes["path"].split("?", 1)[0].split("#", 1)[0]
        for key, length in (("trace_id", 32), ("span_id", 16)):
            if key in attributes and not re.fullmatch("[0-9a-f]{" + str(length) + "}", str(attributes[key])):
                attributes.pop(key)
        record = {"id": uuid4().hex, "timestamp": _now(), "severity_text": severity,
                  "severity_number": _levels[severity], "service.name": self.service_name,
                  "body": _text(str(event))[:256], "actor": actor_identity(actor if actor is not None else current["actor"]),
                  "request_id": _text(str(request_id if request_id is not None else current["request_id"]))[:128],
                  "attributes": attributes, "segment_id": self.segment_id}
        with self._guard:
            if self._closed:
                raise RuntimeError("Structured log store is closed")
            record["segment_id"] = self.segment_id
            raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            maximum = min(self.max_bytes - 1, 65535)
            if len(raw.encode("utf-8")) > maximum:
                record["attributes"] = {"truncated": True}
                for field in ("body", "actor", "request_id", "service.name"):
                    record[field] = record[field][:32]
                raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            if self.path.stat().st_size + len(raw.encode("utf-8")) + 1 >= self.max_bytes:
                self._handler.doRollover()
                self.segment_id = uuid4().hex
                record["segment_id"] = self.segment_id
                raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            self._handler.handle(logging.LogRecord("motor.requests", logging.INFO, "", 0, raw, (), None))
            self._handler.flush()
            self.path.chmod(0o600)
            with self._connect() as db:
                self._insert(db, record)
                retained = self._retained_segments()
                if retained:
                    db.execute("DELETE FROM request_logs WHERE segment NOT IN (" + ",".join("?" for _ in retained) + ")", tuple(retained))
            return record

    @staticmethod
    def _filters(*, severity=None, actor=None, request_id=None, event=None, path=None, status_code=None,
                 since=None, until=None, q=None):
        clauses, values = [], []
        for field, value in (("severity", severity), ("actor", actor), ("request_id", request_id),
                             ("event", event), ("path", path), ("status_code", status_code)):
            if value is not None:
                clauses.append(field + "=?")
                values.append({"WARNING": "WARN", "CRITICAL": "FATAL"}.get(str(value).upper(), str(value).upper())
                              if field == "severity" else value)
        for operation, value in ((">=", timestamp_filter(since)), ("<=", timestamp_filter(until))):
            if value is not None:
                clauses.append("time" + operation + "?")
                values.append(value)
        if q is not None:
            if not isinstance(q, str) or len(q) > 512:
                raise ValueError("Log query must contain at most 512 characters")
            clauses.append("body LIKE ? ESCAPE '\\'")
            values.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), values

    def query(self, *, limit: int = 100, offset: int = 0, **filters) -> dict:
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError("Log page must use limit 1..1000 and offset 0..100000")
        where, values = self._filters(**filters)
        with self._guard, self._connect() as db:
            total = db.execute("SELECT COUNT(*) FROM request_logs" + where, values).fetchone()[0]
            rows = db.execute("SELECT body FROM request_logs" + where + " ORDER BY rowid DESC LIMIT ? OFFSET ?",
                              [*values, limit, offset]).fetchall()
        return {"items": [redact(json.loads(row[0])) for row in rows], "total": total, "limit": limit,
                "offset": offset, "has_more": offset + len(rows) < total,
                "retention": {"segment_bytes": self.max_bytes, "rotations": self.backup_count,
                              "maximum_file_window_bytes": self.max_bytes * (self.backup_count + 1)},
                "reindex_skipped": self.reindex_skipped}

    def export_jsonl(self, *, limit: int = 10000, offset: int = 0, **filters):
        if type(limit) is not int or not 1 <= limit <= 10000 or type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError("Log export must use limit 1..10000 and offset 0..100000")
        where, values = self._filters(**filters)
        output = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        try:
            with self._guard, self._connect() as db:
                cursor = db.execute("SELECT body FROM request_logs" + where + " ORDER BY rowid DESC LIMIT ? OFFSET ?",
                                    [*values, limit, offset])
                for row in cursor:
                    output.write((json.dumps(redact(json.loads(row[0])), ensure_ascii=False) + "\n").encode("utf-8"))
            output.seek(0)
        except Exception:
            output.close()
            raise

        def stream():
            try:
                while chunk := output.read(65536):
                    yield chunk
            finally:
                output.close()
        return stream()

    def close(self):
        with self._guard:
            if not self._closed:
                self._handler.close()
                self._closed = True
