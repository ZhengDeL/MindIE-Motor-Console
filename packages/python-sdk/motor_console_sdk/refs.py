"""Typed references for commonly consumed stable core ports."""
from .contracts import ContractRef
from .protocols import StorageV1, AuditV1, SessionV1, PolicyV1
from .workflows import ServiceWorkflowV1, TaskCoordinatorV1

STORAGE = ContractRef[StorageV1]("storage.store")
AUDIT = ContractRef[AuditV1]("audit.sink")
SESSION = ContractRef[SessionV1]("security.session")
POLICY = ContractRef[PolicyV1]("security.policy")
SERVICE_WORKFLOW = ContractRef[ServiceWorkflowV1]("services.workflow")
TASK_COORDINATOR = ContractRef[TaskCoordinatorV1]("tasks.coordinator")
