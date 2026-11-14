"""Reference Agent HTTP factories; mandatory write guards remain in the host."""
from fastapi import APIRouter


def factory(ctx):
    state = ctx.require("application_state")
    plugins = state.plugins
    agent_policy = ctx.consume("security.agent")
    if ctx.contract == "api.agent-docker":
        from ..node_agent import create_agent_router
        docker_runtime = ctx.consume("runtime.docker") if plugins.enabled("motor-docker") else None
        kubernetes_runtime = ctx.consume("runtime.kubernetes") if plugins.enabled("motor-kubernetes") else None
        state.docker_runtime, state.kubernetes_runtime = docker_runtime, kubernetes_runtime
        router = create_agent_router(runtime=docker_runtime, kubernetes_runtime=kubernetes_runtime,
            docker_enabled=plugins.enabled("motor-docker"), kubernetes_enabled=plugins.enabled("motor-kubernetes"),
            host_telemetry=ctx.consume("telemetry.host"), agent_policy=agent_policy,
            operation_tracker=state.operation_tracker, native_adapter=ctx.consume("motor.native"))
    elif ctx.contract == "api.agent-kubernetes":
        if not plugins.enabled("motor-kubernetes"):
            return APIRouter()
        from ..kubernetes_runtime import create_kubernetes_router
        runtime = ctx.consume("runtime.kubernetes")
        state.kubernetes_runtime = runtime
        router = create_kubernetes_router(runtime, agent_policy=agent_policy, operation_tracker=state.operation_tracker)
    else:
        raise ValueError("Unsupported reference Agent HTTP contract: " + ctx.contract)
    router.routes[:] = [route for route in router.routes
        if not (owner := plugins.owner_for_path(getattr(route, "path", ""))) or plugins.enabled(owner)]
    return router
