"""Independent business service and HTTP module using only the public SDK."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, TypedDict, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from motor_console_sdk import AUDIT, STORAGE, AuditV1, ContractRef, ProviderContext, StorageV1


class Note(TypedDict):
    id: str
    text: str
    created_at: str


class NotesPage(TypedDict):
    items: list[Note]
    total: int
    limit: int
    offset: int
    has_more: bool


class NotesV1(Protocol):
    def list_notes(self) -> NotesPage: ...
    def create_note(self, text: str) -> Note: ...


NOTES = ContractRef[NotesV1]("ext.example-notes.notes", 1)
KIND = "plugin:example-notes"


class NotesService:
    def __init__(self, storage: StorageV1, audit: AuditV1):
        self._storage, self._audit = storage, audit

    def list_notes(self) -> NotesPage:
        # The selected Store owns its transaction and backup; this module does
        # not open SQLite/PostgreSQL or close another provider's connection.
        return cast(NotesPage, self._storage.page(KIND, limit=50))

    def create_note(self, text: str) -> Note:
        text = text.strip()
        if not 1 <= len(text) <= 2000:
            raise ValueError("Note text must contain 1 to 2000 characters")
        note: Note = {"id": "note_" + uuid4().hex, "text": text,
                      "created_at": datetime.now(timezone.utc).isoformat()}
        self._audit.mutate_audited(KIND, note["id"], dict(note),
            action="example-notes.create", metadata={"note_id": note["id"]})
        return note


class NoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(strict=True, min_length=1, max_length=2000)


def create(context: ProviderContext) -> object:
    if context.contract == NOTES.id:
        return NotesService(context.consume(STORAGE), context.consume(AUDIT))
    if context.contract != "ext.example-notes.http":
        raise ValueError("This package does not implement the requested contract")
    service = context.consume(NOTES)
    router = APIRouter()

    @router.get("/api/plugins/example-notes/notes")
    def list_notes():
        return service.list_notes()

    @router.post("/api/plugins/example-notes/notes", status_code=201)
    def create_note(body: NoteInput):
        if not body.text.strip():
            raise HTTPException(422, "Please enter note text")
        return {"item": service.create_note(body.text)}

    return router
