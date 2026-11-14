"""Public synchronous provider SDK v1, shared by reference and installed packages.

Providers are trusted code. Version and artifact checks prevent accidental
composition drift; they are not a code sandbox. HTTP modules return routers
and never install middleware or alter another module's routes.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Callable, Mapping

from ..plugin_host.contracts import PluginConfigurationError, synchronous_cleanup

from motor_console_sdk.contracts import (Contract, ContractRef, Provider, SDK_VERSION,
    NativeRenderContextV1, AdmissionContextV1)

class ProviderContext:
    """An immutable factory scope; business dependencies use exact contracts.

    ``inputs`` is documented at each call site in the SDK catalogue. It holds
    invocation data, never an implicit fallback to a reference business class.
    ``consume`` resolves the selected instance for the entire host generation.
    """

    sdk_version = SDK_VERSION

    def __init__(self, host, provider: Provider, contract: str, inputs: Mapping[str, Any]):
        self._host, self.provider, self.contract = host, provider, contract
        self.settings = MappingProxyType({key: value for key, value in host.settings.items()
            if (key != "startup" or contract == "console.application")
            and (not contract.startswith("ext.") or key in {"mode", "data_dir", "upstream_dir"})})
        self.inputs = MappingProxyType(dict(inputs))
        self.generation = host.generation

    def consume(self, contract: str | ContractRef, version: int | None = None, **inputs) -> Any:
        if isinstance(contract, ContractRef):
            if version is not None and version != contract.version:
                raise PluginConfigurationError("Typed contract reference conflicts with explicit version")
            contract, version = contract.id, contract.version
        elif version is None:
            version = 1
        if contract not in (self.provider.contract_dependencies[self.contract] if self.provider.contract_dependencies is not None else self.provider.dependencies):
            raise PluginConfigurationError(f"Provider {self.provider.id} did not declare dependency {contract}")
        value = self._host.resolve(contract, version, **inputs)
        if self.contract.startswith("ext.") and value is not None:
            return _PublicProduct(value, self._host._contracts[contract])
        return value

    def require(self, name: str) -> Any:
        if name not in self.inputs:
            raise PluginConfigurationError("Missing provider input: " + name)
        value = self.inputs[name]
        if isinstance(value, _LazyInput):
            if not value.ready and (self._host._sealed or self.contract not in self._host._constructing):
                raise PluginConfigurationError("Reference route construction requires its active synchronous factory scope")
            return value.get()
        return value

    def on_shutdown(self, callback: Callable, /, *args, **kwargs) -> None:
        if self._host._sealed or self.contract not in self._host._constructing:
            raise PluginConfigurationError("Provider cleanup registration requires its active synchronous factory scope")
        self._host.cleanup.callback(synchronous_cleanup(callback), *args, **kwargs)

    def binding(self, contract: str) -> dict:
        return self._host.binding(contract)

    def is_reference(self, contract: str) -> bool:
        return self._host.is_reference(contract)

    def describe(self) -> dict:
        return {**self._host.describe(), "generation": self.generation}

    def sdk_description(self) -> dict:
        return self._host.sdk_description()

    def prepare_api_routes(self, application, contracts) -> None:
        if self.contract != "console.application":
            raise PluginConfigurationError("Only the host application assembler can prepare selected HTTP modules")
        self._host.prepare_api_routes(application, contracts)

    def select_api_routes(self, application) -> None:
        if self.contract != "console.application":
            raise PluginConfigurationError("Only the selected application assembler can finalize HTTP modules")
        self._host.select_api_routes(application)


__all__ = ["SDK_VERSION", "Contract", "Provider", "ProviderContext", "NativeRenderContextV1", "AdmissionContextV1"]


class _PublicProduct:
    """Expose selected contract members, not an implementation's private fields.

    Trusted Python remains able to inspect its process; this is an API boundary,
    not a claim of adversarial code isolation.
    """
    __slots__ = ("_value", "_allowed")

    def __init__(self, value, contract):
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_allowed", frozenset((*contract.methods, *contract.attributes)))

    def __getattribute__(self, name):
        if name not in object.__getattribute__(self, "_allowed"):
            raise AttributeError("Member is outside the selected public contract: " + name)
        return getattr(object.__getattribute__(self, "_value"), name)

    def __setattr__(self, name, value):
        raise AttributeError("Selected public contract views are read-only; use declared methods")

    def __call__(self, *args, **kwargs):
        if "__call__" not in object.__getattribute__(self, "_allowed"):
            raise TypeError("Selected contract is not callable")
        return object.__getattribute__(self, "_value")(*args, **kwargs)


class _LazyInput:
    """A compatibility capability evaluated only by an explicit require()."""
    def __init__(self, factory):
        self.factory, self.attempted, self.ready, self.value = factory, False, False, None

    def get(self):
        if self.ready:
            return self.value
        if self.attempted:
            raise PluginConfigurationError("Reference route construction already failed; restart is required")
        self.attempted = True
        self.value = self.factory()
        self.ready = True
        return self.value
