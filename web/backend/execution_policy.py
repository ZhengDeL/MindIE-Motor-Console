"""Explicit execution mode, independent of network authentication and runtime.

New Agents start in observation mode. Enabling management is a deployment-owned
decision after resource ownership has been reconciled; an HTTP payload cannot
change this policy or adopt resources from another product.
"""
from __future__ import annotations

import os


class AgentReadOnlyError(PermissionError):
    """This Agent is intentionally unable to mutate host or cluster resources."""


def agent_read_only() -> bool:
    raw = os.getenv("MOTOR_AGENT_READ_ONLY", "true")
    if raw.lower() not in {"true", "false"}:
        raise ValueError("MOTOR_AGENT_READ_ONLY must be exactly true or false")
    return raw.lower() == "true"


def execution_metadata(read_only: bool | None = None) -> dict:
    readonly = agent_read_only() if read_only is None else read_only
    if type(readonly) is not bool:
        raise ValueError("Agent execution mode must be a boolean")
    return {"read_only": readonly, "execution_mode": "read_only" if readonly else "managed"}


def require_mutations(read_only: bool | None = None) -> None:
    if execution_metadata(read_only)["read_only"]:
        raise AgentReadOnlyError(
            "Agent is in read-only mode; resource ownership must be explicitly reconciled "
            "before management is enabled by the deployment administrator"
        )
