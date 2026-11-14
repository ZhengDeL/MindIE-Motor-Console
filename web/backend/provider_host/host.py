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
from .contracts import Provider, ProviderContext, _LazyInput
from motor_console_sdk.contracts import ProviderResult
from motor_console_sdk.validation import extension_contracts, http_modules, validate_product

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
        self._contracts = MappingProxyType(dict(CONTRACTS))
        self._http_modules = ()
        import motor_console_sdk
        sdk_root = Path(motor_console_sdk.__file__).resolve().parent
        sdk_digest = hashlib.sha256()
        for path in sorted(sdk_root.glob("*.py")):
            sdk_digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes())
        self._sdk_artifact = {"version": motor_console_sdk.__version__, "sha256": sdk_digest.hexdigest()}
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
                                "external_artifacts": self._external_artifacts,
                                "contracts": self.describe()["contracts"], "http_modules": self._http_modules, "sdk": self._sdk_artifact}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        self.generation = hashlib.sha256(canonical.encode()).hexdigest()

    def set_bootstrap_artifacts(self, name, signature):
        """Host bootstrap only, before any factory executes or receives context."""
        if self._attempted or self._sealed or name in self._external_artifacts:
            raise PluginConfigurationError("Bootstrap artifact identity must be fixed before any factory executes")
        if name == "legacy-additive-packages":
            legacy_ids = {item.get("id") for item in signature}
            collisions = legacy_ids & {item["namespace"] for item in self._http_modules}
            if collisions:
                raise PluginConfigurationError("HTTP namespace conflicts with selected legacy .qhplugin: " + ", ".join(sorted(collisions)))
        self._external_artifacts[name] = json.loads(json.dumps(signature, allow_nan=False))
        self._refresh_generation()

    def _configure(self, document):
        base = {"schemaVersion", "providers", "bindings"}
        if (not isinstance(document, dict) or type(document.get("schemaVersion")) is not int
                or document["schemaVersion"] not in {1, 2}
                or not base <= set(document)
                or set(document) - base - ({"contract_definitions", "http_modules"} if document["schemaVersion"] == 2 else set())):
            raise PluginConfigurationError("Provider assembly requires schemaVersion=1 or 2, providers and bindings")
        if (not isinstance(document["providers"], list) or len(document["providers"]) > 256
                or not isinstance(document["bindings"], dict)):
            raise PluginConfigurationError("Provider declarations and bindings have invalid types")
        try:
            extensions = extension_contracts(document.get("contract_definitions", []), CONTRACTS)
            self._contracts = MappingProxyType({**CONTRACTS, **extensions})
            self._http_modules = http_modules(document.get("http_modules", []), self._contracts)
        except ValueError as error:
            raise PluginConfigurationError(str(error)) from error
        for name in extensions:
            if name not in document["bindings"]:
                raise PluginConfigurationError("Extension contracts require explicit exact bindings: " + name)
        for item in document["providers"]:
            required = {"id", "version", "contracts", "dependencies", "module_file", "sha256", "factory", "trust_code"}
            if (not isinstance(item, dict) or not required <= set(item)
                    or set(item) - required - ({"contract_dependencies"} if document["schemaVersion"] == 2 else set())):
                raise PluginConfigurationError("Installed provider declaration has an invalid schema")
            identifier, version = item["id"], item["version"]
            if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier.startswith(("reference.", "core.", "internal.")) or identifier in self._providers or not isinstance(version, str) or not _VERSION.fullmatch(version):
                raise PluginConfigurationError("Provider identity is invalid or duplicated")
            if item["trust_code"] is not True:
                raise PluginConfigurationError("Installed provider code requires explicit trust_code=true")
            if not isinstance(item["contracts"], dict) or not item["contracts"] or any(name not in self._contracts or self._contracts[name].internal or type(version) is not int or version != self._contracts[name].version for name, version in item["contracts"].items()):
                raise PluginConfigurationError("Installed provider declares an unsupported contract version")
            dependencies = item["dependencies"]
            if isinstance(dependencies, dict) and document["schemaVersion"] == 2:
                if any(name not in self._contracts or type(version) is not int or version != self._contracts[name].version
                       for name, version in dependencies.items()):
                    raise PluginConfigurationError("Provider dependency version mismatch")
                dependencies = list(dependencies)
            if not isinstance(dependencies, list) or any(not isinstance(value, str) or value not in self._contracts or self._contracts[value].internal for value in dependencies) or len(set(dependencies)) != len(dependencies):
                raise PluginConfigurationError("Installed provider dependencies are invalid")
            scoped_dependencies = None
            if "contract_dependencies" in item:
                scoped = item["contract_dependencies"]
                if dependencies or not isinstance(scoped, dict) or set(scoped) != set(item["contracts"]):
                    raise PluginConfigurationError("Per-contract dependencies must cover all provided contracts with empty global dependencies")
                scoped_dependencies = {}
                for owner, requirements in scoped.items():
                    if (not isinstance(requirements, dict) or any(name not in self._contracts
                            or self._contracts[name].internal or type(version) is not int
                            or version != self._contracts[name].version for name, version in requirements.items())):
                        raise PluginConfigurationError("Per-contract dependency has an unknown contract or incompatible exact version")
                    scoped_dependencies[owner] = tuple(requirements)
                scoped_dependencies = MappingProxyType(scoped_dependencies)
            path = Path(item["module_file"])
            if not path.is_absolute() or not path.is_file() or path.is_symlink() or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"] or "") or not isinstance(item["factory"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item["factory"]):
                raise PluginConfigurationError("Installed provider requires a regular absolute module_file, SHA256 and factory name")
            # Verification is side-effect-free and precedes importing *any*
            # selected module. Recheck again immediately before compile.
            from ..plugin_host.packages.files import read_regular
            raw = read_regular(path, 4 * 1024 * 1024)
            if len(raw) > 4 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise PluginConfigurationError("Installed provider artifact digest mismatch: " + identifier)
            try:
                compile(raw.decode("utf-8"), str(path), "exec", dont_inherit=True)
            except (UnicodeError, SyntaxError) as error:
                raise PluginConfigurationError("Installed provider must contain valid UTF-8 Python: " + identifier) from error
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
                        exec(compile(content.decode("utf-8"), str(verified_path), "exec", dont_inherit=True), module.__dict__)
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
                                                  tuple(dependencies), item["sha256"], scoped_dependencies)
        for contract, value in document["bindings"].items():
            if contract in self._contracts and self._contracts[contract].internal:
                raise PluginConfigurationError("Host assembly/enforcement is internal and cannot be overridden: " + contract)
            if contract in self._contracts and self._contracts[contract].optional and value is None:
                value = {"provider": None, "version": None, "contract_version": self._contracts[contract].version}
            if contract not in self._contracts or not isinstance(value, dict) or set(value) != {"provider", "version", "contract_version"}:
                raise PluginConfigurationError("Unknown contract or invalid exact binding: " + str(contract))
            self._bindings[contract] = dict(value)
            self._overrides.add(contract)

    def _validate_graph(self):
        for contract, binding in self._bindings.items():
            if binding["provider"] is None and self._contracts[contract].optional:
                if binding["version"] is not None or type(binding["contract_version"]) is not int or binding["contract_version"] != self._contracts[contract].version:
                    raise PluginConfigurationError("Unbound optional contract requires its exact version and no provider version: " + contract)
                continue
            if (not isinstance(binding["provider"], str) or not _ID.fullmatch(binding["provider"])
                    or not isinstance(binding["version"], str) or not _VERSION.fullmatch(binding["version"])):
                raise PluginConfigurationError("Provider binding requires exact literal identity and semantic version: " + contract)
            provider = self._providers.get(binding["provider"])
            if provider is None or provider.version != binding["version"] or contract not in provider.contracts or type(binding["contract_version"]) is not int or binding["contract_version"] != self._contracts[contract].version:
                raise PluginConfigurationError("Missing provider or incompatible exact contract binding: " + contract)
        for contract, binding in self._bindings.items():
            provider = self._providers.get(binding["provider"])
            requirements = self.dependencies_for(contract) if provider else ()
            if contract != "console.application" and any(name not in self._bindings or self._bindings[name]["provider"] is None and not self._contracts[name].optional for name in requirements):
                raise PluginConfigurationError("Selected provider has an unbound dependency: " + contract)
        visited, stack = set(), []
        def visit(contract):
            if contract in stack:
                raise PluginConfigurationError("Provider dependency cycle: " + " -> ".join([*stack, contract]))
            if contract in visited:
                return
            stack.append(contract)
            provider = self._providers.get(self._bindings[contract]["provider"])
            for dependency in self.dependencies_for(contract) if provider is not None else ():
                visit(dependency)
            stack.pop()
            visited.add(contract)
        for contract in self._bindings:
            visit(contract)

    def dependencies_for(self, contract):
        provider = self._providers.get(self._bindings[contract]["provider"])
        if provider is None:
            return ()
        return provider.contract_dependencies[contract] if provider.contract_dependencies is not None else provider.dependencies

    def is_reference(self, contract):
        return self._bindings[contract]["provider"] == "reference." + contract

    def binding(self, contract):
        if contract not in self._bindings:
            raise PluginConfigurationError("Unknown provider contract: " + str(contract))
        provider = self._providers.get(self._bindings[contract]["provider"])
        return {"contract": contract, **self._bindings[contract], "artifact": provider.artifact if provider else None,
                "contract_definition": self.contract_definition(contract),
                "dependencies": {name: self._contracts[name].version for name in self.dependencies_for(contract)}}

    def describe(self):
        public = [name for name in sorted(self._bindings) if not self._contracts[name].internal]
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
                "external_artifacts": copy.deepcopy(self._external_artifacts),
                "contracts": [self.contract_definition(name) for name in sorted(self._contracts)],
                "http_modules": copy.deepcopy(list(self._http_modules)), "sdk": dict(self._sdk_artifact)}

    def sdk_description(self):
        from dataclasses import asdict
        from .sdk import product_shape
        modules = {item["contract"]: item for item in self._http_modules}
        def shape(name):
            if name in modules:
                return {"kind": "APIRouter | tuple[BaseRoute, ...]", "inputs": [],
                    **copy.deepcopy(modules[name]), "authorization": "mandatory selected Session/Policy, roles, CSRF and audit",
                    "mode": "control", "nested_routers": False, "websockets": False}
            if name in CONTRACTS:
                return product_shape(name)
            spec = self._contracts[name]
            return {"kind": "object", "methods": list(spec.methods), "attributes": list(spec.attributes)}
        return {"schemaVersion": 1, "sdk_version": 1, "sdk": dict(self._sdk_artifact),
            "contracts": [{**asdict(self._contracts[name]), "product": shape(name)}
                for name in sorted(self._contracts) if not self._contracts[name].internal],
            "context": {"settings": ["data_dir", "upstream_dir", "mode", "configuration", "console_token"],
                        "extension_settings": ["data_dir", "upstream_dir", "mode"],
                        "methods": ["consume(contract, version=1, **inputs)", "consume(ContractRef[T]) -> T",
                                    "require(name)", "on_shutdown(callback, *args, **kwargs)", "binding(contract)"]},
            "lifecycle": "synchronous bootstrap; reverse-order cleanup; restart-only selection"}

    def resolve(self, contract: str, version: int = 1, **inputs):
        spec = self._contracts.get(contract)
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
            if isinstance(value, (PluginInstance, ProviderResult)):
                if value.close:
                    ProviderContext(self, provider, contract, inputs).on_shutdown(value.close)
                value = value.value
            try:
                validate_product(spec, value)
            except ValueError as error:
                raise PluginConfigurationError(str(error)) from error
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
            inputs = self._api_factory_inputs(application, contract, tuple(grouped.get(contract, ())))
            value = self.resolve(contract, **inputs)
            routes = getattr(value, "routes", value)
            if not isinstance(routes, (tuple, list)):
                raise PluginConfigurationError("HTTP provider must return an APIRouter or route sequence: " + contract)
            for route in routes:
                if route_contract(getattr(route, "path", "")) != contract:
                    raise PluginConfigurationError("HTTP provider route is outside contract ownership: " + str(getattr(route, "path", "")))
                route_methods[id(route)] = http_route_methods(contract, route)
                retained.append(route)
                if hasattr(value, "routes"):
                    route_sources.setdefault(id(route), (value, {}))
            for callback in getattr(value, "on_shutdown", ()):
                self.cleanup.callback(callback)
        if self._http_modules and self.settings.get("mode") == "control":
            from .http import protect_extension_route
            session, policy = self.resolve("security.session"), self.resolve("security.policy")
            audit = self.resolve("audit.sink")
            for module in self._http_modules:
                value = self.resolve(module["contract"])
                routes = getattr(value, "routes", value)
                if not isinstance(routes, (tuple, list)):
                    raise PluginConfigurationError("Extension HTTP factory must return direct routes or APIRouter")
                expected = {(item["path"], method): item for item in module["routes"] for method in item["methods"]}
                actual = set()
                for route in routes:
                    methods = http_route_methods(module["contract"], route)
                    for method in methods:
                        key = (getattr(route, "path", None), method)
                        if key not in expected or key in actual:
                            raise PluginConfigurationError("Extension HTTP route/method differs from validated declaration: " + repr(key))
                        actual.add(key)
                    secured = protect_extension_route(route, self, session, policy, audit, expected)
                    route_methods[id(secured)] = methods
                    retained.append(secured)
                if actual != set(expected):
                    raise PluginConfigurationError("Extension HTTP factory omitted declared routes or methods")
                for callback in getattr(value, "on_shutdown", ()):
                    from ..plugin_host.contracts import synchronous_cleanup
                    self.cleanup.callback(synchronous_cleanup(callback))
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

    def _api_factory_inputs(self, application, contract, captured):
        inputs = {"reference_routes": captured, "application_state": application.state,
                  "api_inputs": getattr(application.state, "reference_api_inputs", None)}
        if not self.is_reference(contract):
            original_inputs = dict(inputs)
            inputs["reference_routes"] = _LazyInput(lambda: self._reference_routes(contract, original_inputs))
        return inputs

    def prepare_api_routes(self, application, contracts):
        """Resolve explicitly requested selected APIs for the legacy v1 bridge.

        This never chooses an unselected reference factory. Final route and
        permission validation remains in select_api_routes after contributions.
        """
        if self._sealed:
            raise PluginConfigurationError("HTTP graph is sealed; preparation requires restart")
        if (not isinstance(contracts, (tuple, list)) or len(set(contracts)) != len(contracts)
                or any(name not in CONTRACTS or not name.startswith("api.") for name in contracts)):
            raise PluginConfigurationError("HTTP preparation requires distinct known API contracts")
        capture = getattr(application, "provider_routes", None)
        routes = capture()[0] if callable(capture) else application.router.routes
        for name in contracts:
            captured = tuple(route for route in routes if route_contract(getattr(route, "path", "")) == name)
            self.resolve(name, **self._api_factory_inputs(application, name, captured))

    def _reference_routes(self, contract, inputs):
        """Legacy SDK capability; independent API factories never call this."""
        from ..plugin_host.contracts import synchronous_cleanup
        reference = self._providers["reference." + contract]
        context = ProviderContext(self, reference, contract, inputs)
        product = reference.factory(context)
        if inspect.isawaitable(product):
            if inspect.iscoroutine(product): product.close()
            raise PluginConfigurationError("Reference route factory returned an awaitable")
        if isinstance(product, (PluginInstance, ProviderResult)):
            if product.close: context.on_shutdown(product.close)
            product = product.value
        routes = getattr(product, "routes", product)
        if not isinstance(routes, (tuple, list)):
            raise PluginConfigurationError("Reference route capability did not return direct routes")
        for callback in getattr(product, "on_shutdown", ()):
            self.cleanup.callback(synchronous_cleanup(callback))
        return tuple(routes)

    def contract_definition(self, contract):
        from dataclasses import asdict
        return json.loads(json.dumps(asdict(self._contracts[contract])))

    def http_roles(self, path, method):
        """Inspect declared permissions; mandatory host policy remains the access ceiling."""
        from starlette.routing import compile_path
        for module in self._http_modules:
            for route in module["routes"]:
                if method in route["methods"] and compile_path(route["path"])[0].fullmatch(path):
                    return frozenset(route["roles"]), route["account_only"]
        return None

    def seal(self):
        self._sealed = True
