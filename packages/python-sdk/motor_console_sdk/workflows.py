"""Public service orchestration v1; only standard library types cross the port.

Records are lossless JSON objects. Providers must preserve unknown Motor fields.
Host callbacks are synchronous and bound to this application generation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class ServiceWorkflowInputsV1:
    mode: str
    upstream: Any
    mutation_lock: Any
    checkpoint: Callable[..., None]
    mark_unhealthy: Callable[[], None]
    require_runtime: Callable[[str], None]


@dataclass(frozen=True)
class ServiceCommandV1:
    approval: dict[str, Any] | None = None
    counts: dict[str, int] | None = None
    confirmation_name: str | None = None
    idempotency_key: str = ""


@dataclass(frozen=True)
class ServiceDeletionV1:
    body: dict[str, Any]
    status_code: int = 200


class ServiceWorkflowV1(Protocol):
    def get(self, identifier: str) -> dict: ...
    def list(self) -> list[dict]: ...
    def create(self, data: dict) -> dict: ...
    def update(self, identifier: str, data: dict) -> dict: ...
    def delete(self, identifier: str) -> ServiceDeletionV1: ...
    def export(self, identifier: str) -> dict: ...
    def revisions(self, identifier: str) -> list[dict]: ...
    def reconcile(self, service: dict) -> dict: ...
    def plan(self, service: dict, *, replacing: bool = False) -> dict: ...
    def require_scalable(self, service: dict) -> None: ...
    def plan_scale(self, service: dict, counts: dict) -> dict: ...
    def execution_placements(self, service: dict, action: str) -> list[dict]: ...
    def payload(self, service: dict, placement: dict, nodes: dict) -> dict: ...
    def render(self, data: dict, previous: dict | None = None) -> dict: ...


@dataclass(frozen=True)
class TaskCoordinatorInputsV1:
    mode: str
    mutation_lock: Any
    checkpoint: Callable[..., None]
    mark_unhealthy: Callable[[], None]
    generation: str
    runtime_binding: Callable[[str], dict]
    workflow_inputs: ServiceWorkflowInputsV1
    execution_enabled: bool = True


class TaskCoordinatorV1(Protocol):
    def submit(self, identifier: str, action: str, command: ServiceCommandV1) -> dict: ...
    def run(self, job_id: str) -> None: ...
    def get(self, identifier: str) -> dict: ...
    def list(self, service_id: str | None = None) -> list[dict]: ...


@dataclass(frozen=True)
class ReferenceApiInputsV1:
    """Host capabilities passed to HTTP factories before the route graph seals.

Business dependencies are obtained through the factory's declared consume()
contracts. This object contains host enforcement, never reference workflows.
"""
    workflow_inputs: ServiceWorkflowInputsV1
    coordinator_inputs: TaskCoordinatorInputsV1
    secured: tuple[Any, ...]
    role_for_request: Callable[[Any], str | None]


__all__ = ["ServiceWorkflowInputsV1", "ServiceCommandV1", "ServiceDeletionV1",
           "ServiceWorkflowV1", "TaskCoordinatorInputsV1", "TaskCoordinatorV1", "ReferenceApiInputsV1"]
