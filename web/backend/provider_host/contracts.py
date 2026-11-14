"""Public synchronous provider SDK v1, shared by reference and installed packages.

Providers are trusted code. Version and artifact checks prevent accidental
composition drift; they are not a code sandbox. HTTP modules return routers
and never install middleware or alter another module's routes.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ..plugin_host.contracts import PluginConfigurationError, synchronous_cleanup

SDK_VERSION = 1


@dataclass(frozen=True)
class NativeRenderContextV1:
    """Capabilities for file-only native rendering, no runtime-private object."""
    upstream: Any
    directory_for: Callable[[str], Any]
    runner: Callable[..., Any]
    verify_source: Callable[..., Any]


@dataclass(frozen=True)
class AdmissionContextV1:
    """Bound observations used by admission; ownership remains in the runtime."""
    host_root: Any
    telemetry: Callable[..., dict]
    container_pids: Callable[..., Any]
    devices: Callable[..., Any]
    labels: Callable[..., Any]
    declared_devices: Callable[..., Any]
    resource_numbers: Callable[..., Any]
    container_ports: Callable[..., Any]
    port_in_use: Callable[..., Any]
    owned_listener_ports: Callable[..., Any]
    physical_check: Callable[..., Any]


@dataclass(frozen=True)
class Contract:
    id: str
    version: int = 1
    methods: tuple[str, ...] = ()
    optional: bool = False
    attributes: tuple[str, ...] = ()
    internal: bool = False


@dataclass(frozen=True)
class Provider:
    id: str
    version: str
    contracts: tuple[str, ...]
    factory: Callable[["ProviderContext"], Any]
    dependencies: tuple[str, ...] = ()
    artifact: str = "reference"


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
            if key != "startup" or contract == "console.application"})
        self.inputs = MappingProxyType(dict(inputs))
        self.generation = host.generation

    def consume(self, contract: str, version: int = 1, **inputs) -> Any:
        if contract not in self.provider.dependencies:
            raise PluginConfigurationError(f"Provider {self.provider.id} did not declare dependency {contract}")
        return self._host.resolve(contract, version, **inputs)

    def require(self, name: str) -> Any:
        if name not in self.inputs:
            raise PluginConfigurationError("Missing provider input: " + name)
        return self.inputs[name]

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

    def select_api_routes(self, application) -> None:
        if self.contract != "console.application":
            raise PluginConfigurationError("Only the selected application assembler can finalize HTTP modules")
        self._host.select_api_routes(application)


__all__ = ["SDK_VERSION", "Contract", "Provider", "ProviderContext", "NativeRenderContextV1", "AdmissionContextV1"]
