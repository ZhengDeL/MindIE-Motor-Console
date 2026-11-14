# Motor Console Python SDK

This independently installable package exports only standard-library DTOs, protocols, storage repository ports and data-only validation helpers. Python 3.11 or later is required. SDK version 1.0.0 and contract `SDK_VERSION=1` are independent of the Console release version.

```sh
python -m pip install --no-deps ./packages/python-sdk
python -m pip wheel --no-deps --no-build-isolation --no-index ./packages/python-sdk
```

The wheel is platform-independent and contains `py.typed`. Offline wheel construction requires the declared build backend, `setuptools==82.0.1`, already installed; no provider tool installs dependencies or runs package hooks.

```python
from motor_console_sdk import ProviderContext, ProviderResult, ContractRef, STORAGE, ServiceCommandV1
from motor_console_sdk.storage import UnitOfWork, RevisionConflict
from motor_console_sdk.validation import extension_contracts, http_modules, validate_product
```

`ContractRef[ProductProtocol](id, version)` gives `ProviderContext.consume()` a concrete product return type while retaining exact runtime dependency and version checks. The predefined `STORAGE`, `AUDIT`, `SESSION`, `POLICY`, `SERVICE_WORKFLOW` and `TASK_COORDINATOR` references name stable core ports. For example, `store = ctx.consume(STORAGE)` exposes `StorageV1`, including transactional `mutate_audited`, `mutate_many_audited` and `compare_and_mutate_audited`; repository transactions contain public `UnitOfWork` values, never native driver connections.

Provider factories are synchronous. Resolve selected dependencies during construction with `ctx.consume("ext.example.logic", version=1)`, register synchronous disposal using `ctx.on_shutdown`, and return the product for `ctx.contract`. `ProviderResult(value, close)` supplies optional disposal. The SDK never imports backend implementations. Legacy `backend.provider_host.sdk` and `backend.ports.storage` imports are compatibility exports for existing packages.

An offline `.mcprovider` schema v2 manifest retains schema v1 fields and can add `contract_definitions`, `http_modules`, and `contract_dependencies`. New contracts require an independent `ext.<namespace>.<name>` identifier and exact version, method and attribute declarations. The host reserves its core definitions and assembly/enforcement interfaces. Every extension contract needs an explicit binding in the startup assembly; installing an artifact does not select it.

A single artifact may provide both logic and HTTP products. Declare its provided versions in `contracts`, set global `dependencies` to `{}`, and use a complete per-contract map such as `{"ext.example.logic": {}, "ext.example.http": {"ext.example.logic": 1}}`. Schema v1 global dependency lists remain supported. Schema v2 also accepts global exact dependency version mappings when no per-contract map is present. All dependencies, bindings, routes and hashes are checked before importing any selected factory.

HTTP contributions declare a nonoptional contract and namespace with complete routes under `/api/plugins/<namespace>`. Each declaration must specify `path`, exact `methods`, explicit authenticated `roles` (`admin`, `operator`, `viewer`), and `account_only`. Viewer is permanently read-only: declarations containing `viewer` may use only GET, HEAD or OPTIONS; unsafe methods must use only administrator/operator roles. Viewer queries use GET/HEAD even when an operation is conceptually a nonmutating POST query. This is validated before any factory runs, so a declaration cannot promise access that the host will deny to every role. Public routes, catchalls, overlapping route shapes, arbitrary ASGI mounts and WebSockets are unsupported. Return an APIRouter or direct route sequence matching the declaration exactly. The host enforces the selected identity/policy, roles, browser CSRF, generation checks and write audit around the route. HTTP contributions run only in Control mode. An active legacy `.qhplugin` cannot share their namespace.

Extension factories receive public settings and declared selected service members; they do not receive native application state. Factories must resolve dependencies before startup seals. A retained context may use an already-created service but cannot lazily start a new product after sealing. Selection changes require restart, and critical custom contract changes are rejected while recorded operations or resources remain unresolved.

Structural validation checks shape and composition. It does not certify deployment behavior, transactional audit semantics or a malicious provider's safety. Installed Python is reviewed, trusted in-process code, not a sandbox. Persistent business mutations must commit domain audit/outbox records in the same unit of work; the mandatory HTTP audit does not replace that responsibility.
