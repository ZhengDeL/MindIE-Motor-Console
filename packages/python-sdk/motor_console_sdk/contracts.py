"""Public composition values and synchronous factory scope; no host imports."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Generic, TypeVar, overload

SDK_VERSION = 1
T = TypeVar("T")


@dataclass(frozen=True)
class ContractRef(Generic[T]):
    """Typed reference to one exact contract version, not an implementation."""
    id: str
    version: int = 1

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id or type(self.version) is not int or self.version < 1:
            raise ValueError("ContractRef requires an identity and exact positive version")

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
    critical: bool = False


@dataclass(frozen=True)
class Provider:
    id: str
    version: str
    contracts: tuple[str, ...]
    factory: Callable[["ProviderContext"], Any]
    dependencies: tuple[str, ...] = ()
    artifact: str = "reference"
    contract_dependencies: Mapping[str, tuple[str, ...]] | None = None



class ProviderContext(Protocol):
    sdk_version: int
    settings: Mapping[str, Any]
    inputs: Mapping[str, Any]
    generation: str
    provider: Provider
    contract: str
    @overload
    def consume(self, contract: ContractRef[T], **inputs: Any) -> T: ...
    @overload
    def consume(self, contract: str, version: int = 1, **inputs: Any) -> Any: ...
    def consume(self, contract: str | ContractRef[Any], version: int | None = None, **inputs: Any) -> Any: ...
    def require(self, name: str) -> Any: ...
    def on_shutdown(self, callback: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None: ...
    def binding(self, contract: str) -> dict: ...
    def is_reference(self, contract: str) -> bool: ...
    def describe(self) -> dict: ...
    def sdk_description(self) -> dict: ...


@dataclass(frozen=True)
class ProviderResult:
    """Optional synchronous disposal owned by the host lifetime."""
    value: Any = None
    close: Callable[[], Any] | None = None
