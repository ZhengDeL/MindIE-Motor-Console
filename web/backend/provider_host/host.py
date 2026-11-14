"""Explicit startup-only provider selection, loaded before persistent storage."""
from __future__ import annotations

from contextlib import ExitStack
import copy
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import sys
from types import MappingProxyType

from ..plugin_host.contracts import PluginConfigurationError, PluginInstance
from .catalogue import CONTRACTS, route_contract
from .contracts import Provider, ProviderContext

_ID = re.compile(r"[a-z][a-z0-9_.-]{0,95}\Z")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise PluginConfigurationError("Duplicate provider configuration key: " + key)
        result[key] = value
    return result


class ProviderHost:
    """One immutable selection graph and lifetime per application generation.

    Installed modules are never scanned, and imported-but-unselected modules
    are never executed. A module is a trusted, reviewed, deployment-installed
    UTF-8 Python artifact, pinned by the SHA256 of its exact bytes. This loader
    deliberately does not import arbitrary environment entry points.
    """

    def __init__(self, *, settings: dict, cleanup: ExitStack, document: dict | None = None):
        from .defaults import reference_providers
        self.settings, self.cleanup = MappingProxyType(dict(settings)), cleanup
        self._sealed = False
        self._providers = {provider.id: provider for provider in reference_providers()}
        self._external_artifacts = {}
        self._bindings = {contract: {"provider": "reference." + contract, "version": self._providers["reference." + contract].version, "contract_version": 1}
                          for contract in CONTRACTS}
        self._instances, self._constructing, self._attempted, self._modules = {}, [], set(), {}
        self._overrides = set()
        if document is None:
            selection_path = os.getenv("MOTOR_PROVIDER_ASSEMBLY_FILE", "")
            if selection_path:
                from ..plugin_host.packages.files import read_regular
                raw = read_regular(Path(selection_path), 1024 * 1024)
                document = json.loads(raw, object_pairs_hook=_pairs)
        if document is not None:
            self._configure(document)
        self._validate_graph()
        self._refresh_generation()

    def _refresh_generation(self):
        canonical = json.dumps({"bindings": self.describe()["bindings"], "host": self.describe()["host"],
                                "external_artifacts": self._external_artifacts}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        self.generation = hashlib.sha256(canonical.encode()).hexdigest()

    def set_bootstrap_artifacts(self, name, signature):
        """Host bootstrap only, before any factory executes or receives context."""
        if self._attempted or self._sealed or name in self._external_artifacts:
            raise PluginConfigurationError("Bootstrap artifact identity must be fixed before any factory executes")
        self._external_artifacts[name] = json.loads(json.dumps(signature, allow_nan=False))
        self._refresh_generation()

    def _configure(self, document):
        if not isinstance(document, dict) or set(document) != {"schemaVersion", "providers", "bindings"} or type(document["schemaVersion"]) is not int or document["schemaVersion"] != 1:
            raise PluginConfigurationError("Provider assembly requires schemaVersion=1, providers and bindings")
        if not isinstance(document["providers"], list) or not isinstance(document["bindings"], dict):
            raise PluginConfigurationError("Provider declarations and bindings have invalid types")
        for item in document["providers"]:
            required = {"id", "version", "contracts", "dependencies", "module_file", "sha256", "factory", "trust_code"}
            if not isinstance(item, dict) or set(item) != required:
                raise PluginConfigurationError("Installed provider declaration has an invalid schema")
            identifier, version = item["id"], item["version"]
            if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier in self._providers or not isinstance(version, str) or not _VERSION.fullmatch(version):
                raise PluginConfigurationError("Provider identity is invalid or duplicated")
            if item["trust_code"] is not True:
                raise PluginConfigurationError("Installed provider code requires explicit trust_code=true")
            if not isinstance(item["contracts"], dict) or not item["contracts"] or any(name not in CONTRACTS or CONTRACTS[name].internal or type(version) is not int or version != CONTRACTS[name].version for name, version in item["contracts"].items()):
                raise PluginConfigurationError("Installed provider declares an unsupported contract version")
            dependencies = item["dependencies"]
            if not isinstance(dependencies, list) or any(not isinstance(value, str) or value not in CONTRACTS or CONTRACTS[value].internal for value in dependencies) or len(set(dependencies)) != len(dependencies):
                raise PluginConfigurationError("Installed provider dependencies are invalid")
            path = Path(item["module_file"])
            if not path.is_absolute() or not path.is_file() or path.is_symlink() or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"] or "") or not isinstance(item["factory"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item["factory"]):
                raise PluginConfigurationError("Installed provider requires a regular absolute module_file, SHA256 and factory name")
            # Verification is side-effect-free and precedes importing *any*
            # selected module. Recheck again immediately before compile.
            from ..plugin_host.packages.files import read_regular
            raw = read_regular(path, 4 * 1024 * 1024)
            if len(raw) > 4 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise PluginConfigurationError("Installed provider artifact digest mismatch: " + identifier)
            def load(context, *, record=dict(item), verified_path=path):
                content = read_regular(verified_path, 4 * 1024 * 1024)
                if hashlib.sha256(content).hexdigest() != record["sha256"]:
                    raise PluginConfigurationError("Provider artifact changed after graph validation")
                name = "_motor_provider_" + self.generation + "_" + record["id"].replace(".", "_").replace("-", "_")
                # A host owns its own module instance; failed/restarted hosts
                # do not reuse a provider's previous mutable module state.
                module = self._modules.get(record["id"])
                if module is None:
                    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(verified_path))
                    module = importlib.util.module_from_spec(spec)
                    module.__file__ = str(verified_path)
                    sys.modules[name] = module
                    try:
                        exec(compile(content, str(verified_path), "exec"), module.__dict__)
                    except BaseException:
                        if sys.modules.get(name) is module: del sys.modules[name]
                        raise
                    self._modules[record["id"]] = module
                    def dispose_module():
                        if sys.modules.get(name) is module: del sys.modules[name]
                    self.cleanup.callback(dispose_module)
                factory = getattr(module, record["factory"], None)
                if not callable(factory) or inspect.iscoroutinefunction(factory):
                    raise PluginConfigurationError("Installed factory must be synchronous and callable")
                return factory(context)
            self._providers[identifier] = Provider(identifier, version, tuple(item["contracts"]), load,
                                                  tuple(dependencies), item["sha256"])
        for contract, value in document["bindings"].items():
            if contract in CONTRACTS and CONTRACTS[contract].internal:
                raise PluginConfigurationError("Host assembly/enforcement is internal and cannot be overridden: " + contract)
            if contract in CONTRACTS and CONTRACTS[contract].optional and value is None:
                value = {"provider": None, "version": None, "contract_version": 1}
            if contract not in CONTRACTS or not isinstance(value, dict) or set(value) != {"provider", "version", "contract_version"}:
                raise PluginConfigurationError("Unknown contract or invalid exact binding: " + str(contract))
            self._bindings[contract] = dict(value)
            self._overrides.add(contract)

    def _validate_graph(self):
        for contract, binding in self._bindings.items():
            if binding["provider"] is None and CONTRACTS[contract].optional:
                continue
            if (not isinstance(binding["provider"], str) or not _ID.fullmatch(binding["provider"])
                    or not isinstance(binding["version"], str) or not _VERSION.fullmatch(binding["version"])):
                raise PluginConfigurationError("Provider binding requires exact literal identity and semantic version: " + contract)
            provider = self._providers.get(binding["provider"])
            if provider is None or provider.version != binding["version"] or contract not in provider.contracts or type(binding["contract_version"]) is not int or binding["contract_version"] != CONTRACTS[contract].version:
                raise PluginConfigurationError("Missing provider or incompatible exact contract binding: " + contract)
        visited, stack = set(), []
        def visit(contract):
            if contract in stack:
                raise PluginConfigurationError("Provider dependency cycle: " + " -> ".join([*stack, contract]))
            if contract in visited:
                return
            stack.append(contract)
            provider = self._providers.get(self._bindings[contract]["provider"])
            for dependency in provider.dependencies if provider is not None else ():
                visit(dependency)
            stack.pop()
            visited.add(contract)
        for contract in self._bindings:
            visit(contract)

    def is_reference(self, contract):
        return self._bindings[contract]["provider"] == "reference." + contract

    def binding(self, contract):
        if contract not in self._bindings:
            raise PluginConfigurationError("Unknown provider contract: " + str(contract))
        provider = self._providers.get(self._bindings[contract]["provider"])
        return {"contract": contract, **self._bindings[contract], "artifact": provider.artifact if provider else None}

    def describe(self):
        public = [name for name in sorted(self._bindings) if not CONTRACTS[name].internal]
        states = []
        for contract in public:
            state = ("unbound" if self._bindings[contract]["provider"] is None else
                     "running" if contract in self._instances else
                     "starting" if contract in self._constructing else
                     "failed" if contract in self._attempted else "selected")
            states.append({"contract": contract, "state": state})
        return {"schemaVersion": 1, "sdk_version": 1, "restart_required": True,
                "sealed": self._sealed, "host": self.binding("console.application"),
                "bindings": [self.binding(contract) for contract in public], "states": states,
                "external_artifacts": copy.deepcopy(self._external_artifacts)}

    def sdk_description(self):
        from dataclasses import asdict
        from .sdk import product_shape
        return {"schemaVersion": 1, "sdk_version": 1,
            "contracts": [{**asdict(CONTRACTS[name]), "product": product_shape(name)} for name in sorted(CONTRACTS) if not CONTRACTS[name].internal],
            "context": {"settings": ["data_dir", "upstream_dir", "mode", "configuration", "console_token"],
                        "methods": ["consume(contract, version=1, **inputs)", "require(name)", "on_shutdown(callback, *args, **kwargs)", "binding(contract)"]},
            "lifecycle": "synchronous bootstrap; reverse-order cleanup; restart-only selection"}

    def resolve(self, contract: str, version: int = 1, **inputs):
        spec = CONTRACTS.get(contract)
        if spec is None or type(version) is not int or spec.version != version:
            raise PluginConfigurationError("Unsupported provider contract: " + str(contract) + " v" + str(version))
        if contract in self._instances:
            return self._instances[contract]
        if self._sealed:
            raise PluginConfigurationError("Provider graph is sealed; new factories require a host restart: " + contract)
        if contract in self._constructing:
            raise PluginConfigurationError("Recursive provider construction: " + contract)
        if contract in self._attempted:
            raise PluginConfigurationError("Provider construction already failed; restart after reviewing effects: " + contract)
        if self._bindings[contract]["provider"] is None:
            return None
        provider = self._providers[self._bindings[contract]["provider"]]
        self._attempted.add(contract)
        self._constructing.append(contract)
        try:
            value = provider.factory(ProviderContext(self, provider, contract, inputs))
            if inspect.isawaitable(value):
                if inspect.iscoroutine(value): value.close()
                raise PluginConfigurationError("Provider factory returned an awaitable: " + contract)
            if isinstance(value, PluginInstance):
                if value.close:
                    ProviderContext(self, provider, contract, inputs).on_shutdown(value.close)
                value = value.value
            if value is None and not spec.optional or value is not None and (any(not callable(getattr(value, method, None)) for method in spec.methods) or any(not hasattr(value, name) for name in spec.attributes)):
                raise PluginConfigurationError("Provider does not implement the selected contract: " + contract)
            self._instances[contract] = value
            return value
        finally:
            self._constructing.pop()

    def select_api_routes(self, application):
        """Select each HTTP module without giving provider code route takeover.

        Reference modules return their captured routes through the same public
        factory input as installed providers. Alternatives can ignore those
        routes and return an independent APIRouter. Mandatory authorization
        middleware remains around the final graph, including new routes.
        """
        from .http import http_route_methods
        grouped, retained, route_methods = {}, [], {}
        capture = getattr(application, "provider_routes", None)
        if callable(capture):
            reference_routes, route_sources = capture()
        else:
            reference_routes, route_sources = application.router.routes, {}
        for route in reference_routes:
            if not isinstance(getattr(route, "path", None), str):
                raise PluginConfigurationError("Application must expose direct routes before provider selection")
            owner = route_contract(getattr(route, "path", ""))
            route_methods[id(route)] = http_route_methods(owner or "host", route)
            if owner and owner.startswith("api."):
                grouped.setdefault(owner, []).append(route)
            else:
                retained.append(route)
        agent_contracts = {"api.agent-docker", "api.agent-kubernetes"}
        for contract in (name for name in CONTRACTS if name.startswith("api.")
                         and (name in agent_contracts) == (self.settings["mode"] == "agent")):
            value = self.resolve(contract, reference_routes=tuple(grouped.get(contract, ())),
                                 application_state=application.state)
            routes = getattr(value, "routes", value)
            if not isinstance(routes, (tuple, list)):
                raise PluginConfigurationError("HTTP provider must return an APIRouter or route sequence: " + contract)
            for route in routes:
                if route_contract(getattr(route, "path", "")) != contract:
                    raise PluginConfigurationError("HTTP provider route is outside contract ownership: " + str(getattr(route, "path", "")))
                route_methods[id(route)] = http_route_methods(contract, route)
                retained.append(route)
                if hasattr(value, "routes"):
                    route_sources[id(route)] = (value, {})
            for callback in getattr(value, "on_shutdown", ()):
                self.cleanup.callback(callback)
        seen = set()
        for route in retained:
            for method in route_methods[id(route)]:
                key = (getattr(route, "path", None), method)
                if key in seen:
                    raise PluginConfigurationError("Duplicate HTTP route after provider selection: " + repr(key))
                seen.add(key)
        # Starlette catches all paths in declaration order. Keep the selected
        # frontend fallback after API routes, regardless of provider ordering.
        selected = [r for r in retained if route_contract(getattr(r, "path", "")) != "api.frontend"] + [r for r in retained if route_contract(getattr(r, "path", "")) == "api.frontend"]
        mount = getattr(application, "mount_provider_routes", None)
        if callable(mount):
            mount(selected, route_sources)
        else:
            application.router.routes[:] = selected

    def seal(self):
        self._sealed = True
