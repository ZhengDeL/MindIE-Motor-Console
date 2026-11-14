"""Real business-router factories, composed only by reviewed host bootstrap.

The factories reuse existing business implementations. Inline service handlers
remain in the host compatibility bridge until their own migration is verified;
declaring a plugin never implies that this migration already happened.
"""
from __future__ import annotations

from typing import Any

from .context import PluginContext
from .contracts import PluginConfigurationError


def _scope(context: PluginContext, identifier: str) -> None:
    if context.plugin_id != identifier:
        raise PluginConfigurationError("Business provider requires its own plugin scope: " + identifier)


def _mount(context: PluginContext, routers: tuple[Any, ...]) -> tuple[Any, ...]:
    secured = context.require_service("secured")
    for router in routers:
        context.mount_router(router, dependencies=secured)
    return routers


def mount_nodes(context: PluginContext) -> Any:
    """Own the rollout manager lifetime and the actual node-management APIs."""
    from ..agent_rollout import AgentRolloutManager, create_agent_rollout_router
    from ..node_management import create_node_router

    _scope(context, "nodes")
    store, fleet, lock, mode, role, sampler = (
        context.require_service(name) for name in
        ("store", "fleet", "mutation_lock", "mode", "role_for_request", "inventory_sampler"))
    # Validate host dependencies before creating a worker-owning manager.
    context.require_service("secured")
    manager = AgentRolloutManager(store, fleet, lock, mode=mode)
    context.on_shutdown(manager.close)
    _mount(context, (
        create_agent_rollout_router(manager, role),
        create_node_router(store, fleet, lock, mode=mode, inventory_sampler=sampler),
    ))
    context.provide("nodes.manager", 1, manager)
    return manager


def mount_services(context: PluginContext) -> tuple[Any, ...]:
    """Compose reviewed placement, endpoint and operation-recovery routes."""
    from ..endpoint_control import create_endpoint_router
    from ..job_recovery import create_recovery_router
    from ..placement_recommendations import create_placement_router

    _scope(context, "services")
    store, fleet, lock, mode, upstream = (context.require_service(name) for name in
                                        ("store", "fleet", "mutation_lock", "mode", "upstream"))
    routers = _mount(context, (
        create_placement_router(store, upstream_dir=upstream),
        create_endpoint_router(store, fleet, mode=mode),
        create_recovery_router(store, fleet, lock, mode=mode),
    ))
    context.provide("services.routes", 1, routers)
    return routers


def mount_gateways(context: PluginContext) -> tuple[Any, ...]:
    """Mount existing-gateway connection APIs without installing a gateway."""
    from ..gateway_registry import create_gateway_router

    _scope(context, "gateways")
    store, fleet, lock, role = (context.require_service(name) for name in
                              ("store", "fleet", "mutation_lock", "role_for_request"))
    routers = _mount(context, (create_gateway_router(store, fleet.secrets, lock, role),))
    context.provide("gateways.routes", 1, routers)
    return routers


def mount_images(context: PluginContext) -> tuple[Any, ...]:
    """Mount the existing node-image APIs and retain their receipt semantics."""
    from ..image_control import create_image_router

    _scope(context, "images")
    store, fleet, lock = (context.require_service(name) for name in ("store", "fleet", "mutation_lock"))
    routers = _mount(context, (create_image_router(store, fleet, lock),))
    context.provide("images.routes", 1, routers)
    return routers


def mount_templates(context: PluginContext) -> tuple[Any, ...]:
    """Mount the original/custom template library with its original RBAC."""
    from ..template_library import create_template_router

    _scope(context, "templates")
    store, official, lock, role = (context.require_service(name) for name in
                                 ("store", "template_list", "mutation_lock", "role_for_request"))
    routers = _mount(context, (create_template_router(store, official, lock, role),))
    context.provide("templates.routes", 1, routers)
    return routers


__all__ = ["mount_nodes", "mount_services", "mount_gateways", "mount_images", "mount_templates"]
