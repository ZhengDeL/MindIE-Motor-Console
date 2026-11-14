# Python Provider SDK v1

`MOTOR_PROVIDER_ASSEMBLY_FILE` selects exact provider versions and contract
versions at startup, before the data store opens. Omit it for reference
providers. Every reference provider uses `ProviderContext`, dependency checks,
product validation and reverse-order cleanup, just like installed providers.
Selections are immutable until restart. The running graph and generation are
returned in `/api/plugins` under `assembly`; `/api/plugin-sdk` includes
`provider_sdk`. Both require an authenticated control-plane request. Neither
returns module paths, tokens or provider settings.

`console.application` is an internal host assembly/enforcement mechanism,
excluded from the public replaceable contract catalogue. Installed packages
cannot claim, depend on or override it. It keeps mandatory authorization,
read-only Agent mode, request audit admission/results and route ownership
around every selected business/API implementation. These mechanisms are the
fixed kernel; their selected identity, session, policy, audit and business
implementations remain replaceable. `states` distinguishes selected bindings
from instantiated running providers; an inactive runtime is not reported as
running merely because it is selected.

```json
{
  "schemaVersion": 1,
  "providers": [{
    "id": "example.inventory",
    "version": "1.0.0",
    "contracts": {"resources.inventory": 1},
    "dependencies": [],
    "module_file": "/opt/console-plugins/example_inventory.py",
    "sha256": "replace-with-the-64-character-sha256-of-the-module",
    "factory": "create",
    "trust_code": true
  }],
  "bindings": {
    "resources.inventory": {
      "provider": "example.inventory",
      "version": "1.0.0",
      "contract_version": 1
    }
  }
}
```

The module is a deployment-installed trusted Python artifact, at an absolute
regular-file path. Its bounded bytes are verified before import and again
before compile. Ancestor links/reparse points, hardlinks and changed artifacts
are rejected. Importing a module executes trusted code; dependency imports are
not sandboxed or transitively pinned. No environment entry points are scanned.
An installed declaration without a binding is verified but never imported.
Use separate provider IDs; do not impersonate a reference ID.

```python
def create(context):
    # resources.inventory v1 returns a callable, not an HTTP router.
    def project(node):
        return {**node, "resource_freshness": "unknown", "npu_total": None}
    return project
```

The independently installable [motor-console-sdk](../../../packages/python-sdk/README.md)
exports public protocols, DTOs, storage ports and typed `ContractRef[T]` values.
SDK distribution version 1.0.0 is independent of Console release versions.
`contracts.py` and `sdk.py` retain legacy imports and implement the host scope; `catalogue.py` is the executable required method/attribute
and ownership catalogue. Factories return one product for `context.contract`.
A package implementing multiple contracts can dispatch on this field. Use
`context.consume(contract, 1)` only for declared dependencies. Dependency
cycles, duplicate IDs/JSON keys, missing bindings, incompatible versions,
untrusted code and invalid products fail startup. Failed factories cannot be
retried in the same host. Dispose workers/connections with
`context.on_shutdown(callback)`; asynchronous bootstrap/disposal is rejected.
No automatic database migrations between different providers are performed.

| Contracts | Factory product / actual caller |
| --- | --- |
| `storage.store` | Storage facade with the public resource/account/audit/UoW ports in `backend/ports/storage.py`; all reference domains. `directory` and `schema_version` are public metadata. |
| `security.identity` | Account/identity facade; account APIs and OIDC. Public methods/attributes are enumerated in `catalogue.py`. Bind the selected session/policy via `bind_security`; implement every account operation or explicitly reject unsupported operations. |
| `security.session`, `security.policy`, `security.agent` | Revocable session facade, authorization facade, local Agent identity facade. `sdk.py` specifies Session/Policy methods; Agent implements `validate()`, `authorize(authorization)` and `metadata()`. Session cookies, origins, CSRF and revocation belong to the selected Session. Default policy depends only on Session, never the reference Identity class. |
| `security.secrets` | `encrypt(str)` / `decrypt(str)` facade, used by Fleet, rollout and gateways. Optional `backup_seed` is reference recovery material; a non-exportable secret provider should select its own maintenance/recovery adapter. |
| `audit.sink` | Transactional audited mutation and event facade. Resource writes and audit must commit together or persist a transactional outbox. Explicit UoW audit appends also pass through the selected sink. |
| `logs.platform`, `logs.runtime` | Structured platform log store and bounded runtime-log reader. HTTP request logging, system queries/exports and native service log APIs use the selected products. |
| `services.workflow`, `tasks.coordinator` | Public service lifecycle/planning and task submission/coordination ports. `ServiceWorkflowInputsV1`, `TaskCoordinatorInputsV1` and `ServiceCommandV1` are in `motor_console_sdk.workflows`; reference implementations live in `reference_modules`. Replacing them does not create unselected reference workflows or schedulers. |
| `tasks.scheduler`, `tasks.recovery`, `demo.seed`, `agent.connection` | Scheduler with `submit/shutdown`, callable startup task reconciliation, callable demo seeding, and Agent transport with `call/cluster_nodes/secrets`; actual jobs and node APIs call these products. |
| `telemetry.inventory`, `telemetry.service-health`, `telemetry.service-metrics`, `telemetry.host` | Node sampler (`start/close`), service observer (`observe/close`), metrics sampler (`sample/close`), and callable host telemetry source. Source identity, units, timestamps, unknown and stale semantics are preserved by implementations. |
| `resources.inventory`, `resources.placement`, `resources.ledger`, `resources.admission` | Callable inventory projection, placement recommendation, ledger factory, and admission facade. Admission receives `AdmissionContextV1`, never another provider's private methods. The public ledger owns operation receipts and allocations. |
| `motor.native`, `runtime.docker`, `runtime.kubernetes` | Native adapter plus Agent runtime objects. Native rendering receives `NativeRenderContextV1`. Runtime method lists are executable in `catalogue.py`; image modules use public `engine_client()` and `resource_ledger()` ports. Original Motor JSON remains Python-native and is not passed through JavaScript numbers. |
| `templates.catalog`, `features.catalog` | Template list and callable capability catalogue; templates and metadata APIs. |
| `integration.grafana`, `maintenance.manager`, `branding.manager` | Grafana facade (`descriptor/status/settings/save/probe/provision/download`), maintenance facade and branding facade. Grafana alone may be explicitly unbound with JSON `null`, producing unavailable functionality rather than a fallback connection. |
| `console.ui-assets` | Callable `(*, directory, mode, store, role_for_request) -> APIRouter`. It exposes `ui_generation` when UI generation checking is supported. |
| `plugins.packages` | Additive package lifecycle/management facade (`declarations/roles_for/mount/catalog/inventory/validate_selection/import_bytes/activate/deactivate`). Its normal factory receives the verified immutable legacy profile snapshot. |
| `api.*` | An independent APIRouter or route sequence. Control reference factories create their routers from selected ports and public host inputs; an alternative creates its own routes. Host checks every path/method for its stable contract and duplicate ownership, then applies the selected authorization policy around it. |

API module contracts are `api.auth`, `api.accounts`, `api.nodes`,
`api.services`, `api.overview`, `api.templates`, `api.images`, `api.gateways`,
`api.runtime-logs`, `api.metrics`, `api.tasks`, `api.audit`, `api.system`,
`api.extensions`, `api.plugins`, `api.frontend`, `api.agent-docker` and
`api.agent-kubernetes`. `route_contract()` is their precise ownership rule.
Control business workflows and routers live in `reference_modules`, outside
`default_console.py`. Selected API factories construct their own routers and
consume selected workflow/task ports. A replacement that explicitly calls
`context.require("reference_routes")` retains the SDK v1 composition capability:
the host constructs the corresponding reference routes once, lazily, with
selected dependencies. An independent replacement that does not request this
input does not construct the reference API. Failed lazy construction cannot be
retried in the same generation. Core replacements retain documented
`application_state` inputs; new extension modules receive no native state.
No module can claim another owner's paths, health/readiness/meta paths, or
avoid selected control/Agent authorization.

Auth API public access is an exact method/path contract: `GET/HEAD
/api/auth/status`, `POST /api/auth/bootstrap`, `POST /api/auth/login`, and
`GET /api/auth/oidc/login` / `GET /api/auth/oidc/callback`. The entire
`/api/auth/users` subtree always requires an administrator browser session;
other auth paths, including unknown extension paths, require an authenticated
browser session. Unsafe methods go through the selected policy's CSRF check.
These guards apply even when a replacement router declares no dependencies.
The selected identity implementation still validates bootstrap credentials,
login credentials and current-secret reauthentication for password changes.
Auth providers cannot claim the separate accounts contract's paths. SDK v1
does not let a package declare an arbitrary new anonymous auth path.

Factory-specific inputs are `registry` for the default Agent connection;
`mutation_lock` for inventory, branding and Grafana; `reference_routes` and
`application_state` and `api_inputs: ReferenceApiInputsV1` for core HTTP modules;
`workflow_inputs` and `coordinator_inputs` for their matching workflow ports. Context settings are `data_dir`,
`upstream_dir`, `mode`, `configuration` and `console_token`.
The host's raw lifetime stack is unavailable to business packages; owned
resources register synchronous disposal in the active factory with
`on_shutdown`. Settings are trusted deployment data and never returned by
the catalogue APIs. Business dependencies use `consume`, not settings.

Legacy active `.qhplugin` package and configuration digests join the full
generation before any factory executes. Metadata loading performs no code
import and creates no directories; execution uses the same immutable verified
snapshot. Changing an additive package or its configuration therefore rejects
writes from a known older UI generation after restart, without changing the
critical database/runtime binding identity.

Critical writes record provider generation and exact runtime binding in jobs;
Docker receipts and Kubernetes ownership records retain their Agent binding.
Unknown operations are not replayed. `provider-generation.json`, separate from
the selected database, prevents a storage/runtime change while recorded
operations or owned resources remain. An existing installation without this
receipt must first start its recorded providers and reconcile; there is no
automatic adoption of a new database/runtime. Code rollback does not undo
external resources or database migrations. This is startup selection, not hot
replacement or real NPU/Kubernetes acceptance.

`provider-agent-effects.json` independently retains Agent pending/unknown
effects and owned resources. Begin is persisted before lifecycle execution;
confirmed removal clears ownership, while stop and unknown outcomes retain
it. Alternative Agent HTTP modules can report typed receipts through public
`application_state.operation_tracker(phase, runtime, action, payload, result)`.
Reference receipt generations are never replaced with the current generation.
UI/API-only and reference bundle-version-only changes do not alter critical
runtime/storage identity; critical source digests and contract bindings do.

Frontend route products are read-only: ordinary HTTP routes declare only
`GET`/`HEAD`. A standard Starlette `Mount` whose app is exactly `StaticFiles`
is the explicit static-file exception and follows the same host method fence.
WebSocket routes, methodless custom routes and arbitrary ASGI mounts are not
part of HTTP SDK v1. Place write extensions in their protected `api.*` slot.
The public `/agent/health` and `/api/meta` exemptions apply only to GET/HEAD.

The kernel calls the selected Session and Policy directly; Identity forwarding
is retained only for account/OIDC implementation compatibility. Policy results
must match the requested allowed roles and required browser-session type.
`authenticate_session(session, request, required=True)` shares a verified result
only on that request and for that exact provider instance. The reference Policy,
Identity and dependency guard cooperate through it; the next request always
revalidates, including after session revocation. No cross-request cache exists.

## Independent contracts and HTTP modules

Assembly and `.mcprovider` schema v2 retain the v1 fields and add explicit
`contract_definitions`, `http_modules` and optional `contract_dependencies`.
Definitions require independent `ext.<namespace>.<name>` IDs, an exact positive
integer version, method and attribute lists, and explicit `optional`/`critical`
booleans. Core/internal definitions cannot be shadowed or extended into another
internal assembly. Every new contract requires an explicit exact binding;
optional definitions can be explicitly unbound. Missing, duplicate, conflicting,
cyclic, incompatible or untrusted metadata fails before any selected factory.
The effective immutable definitions, exact dependencies, HTTP declarations and
SDK source/version identity participate in generation diagnostics.

A single artifact can serve `ext.example.logic` and `ext.example.http`: set
`dependencies` to `{}` and declare `contract_dependencies` as
`{"ext.example.logic": {}, "ext.example.http": {"ext.example.logic": 1}}`.
Each provided contract must have exactly one entry. An alternative schema v2
form uses global `dependencies: {"storage.store": 1}`; schema v1 global lists
remain supported. Dependency mappings specify exact contract versions. Typed
`ctx.consume(ContractRef[LogicV1]("ext.example.logic", 1))` returns the protocol's
static type and applies the same runtime dependency/version checks.

Each HTTP module names its namespace and nonoptional product contract, and
lists complete `path`/`methods`/`roles`/`account_only` declarations. Paths must
stay under `/api/plugins/<namespace>`; anonymous permissions, catchalls,
overlapping route shapes, WebSockets and arbitrary ASGI mounts are rejected.
The returned routes must match the declaration exactly. Control mode mounts
the module automatically, around mandatory selected Session/Policy checks,
verified roles, direct browser CSRF, generation and write audit enforcement.
Viewer remains permanently read-only: any declaration containing `viewer` must
use GET, HEAD or OPTIONS. Unsafe methods require only admin/operator roles;
a viewer-only POST query is rejected during metadata validation rather than
accepted into an unusable all-403 route. Viewer query APIs use GET/HEAD.
An active legacy `.qhplugin` cannot share its namespace. New extension factories
receive only public mode/path settings and declared selected contract members,
not core configuration secrets or native application state.

Mark stateful extension contracts `critical: true` when changing their selected
implementation/shape/dependencies requires reconciling pending work or owned
resources. Their exact definitions and bindings join the independent generation
receipt. A switch with unresolved work fails closed; an existing state directory
without a receipt cannot silently claim a different critical implementation.
Interface validation is structural, not behavior certification. Installed code
is trusted in-process Python, not a sandbox or an automatically installed package.

## Upgrading receipts that predate workflow identities

The earlier `provider-generation.json` schema version 1 recorded five critical
contracts: storage, Docker/Kubernetes runtime, native Motor adapter and the
resource ledger. It did not record `services.workflow` or `tasks.coordinator`,
and its reference-binding serialization deliberately omitted release versions.
Consequently, a reference provider name and an old full-generation hash alone
do not prove that service/task code is unchanged or compatible.

If such a receipt still records pending operations, owned resources or Agent
unknown-effect receipts, startup now fails with the explicit
`provider_receipt_migration_required` diagnostic. This includes an ordinary
reference-only upgrade with live owned resources. The guard leaves the old
receipt unchanged; it does not silently adopt the new workflow implementation.
There is no generic automatic live-resource migration from that format.

1. Retain the original receipt, database, Agent operation/allocation ledgers,
   selected assembly and immutable release/provider artifacts. Restore the exact
   recorded release and assembly using their verified artifacts; a version label
   or `reference.*` name alone is insufficient evidence.
2. On that recorded release, reconcile every pending or unknown operation using
   its original operation ID and actual resource ownership. Do not replay
   requests. If upgrading this installation, complete its normal controlled
   undeploy/removal workflow and confirm resources no longer exist; stopping a
   service alone does not release ownership.
3. Let that release checkpoint the reconciled state. Confirm `pending` and
   `resources` are empty and no Agent effect remains. Take a consistent backup,
   then stop the old writer. Do not hand-edit/delete receipts or clear resource
   flags to make these checks pass.
4. Start the new release with the intended explicit assembly. An honestly idle
   old receipt is allowed; the normal new checkpoint records the workflow
   identities. Check health, API access and required business behavior before
   accepting the upgrade. Retain the old artifacts and backup for recovery.

Keeping live resources through an upgrade requires a separately reviewed,
version-specific migration with complete original workflow/execution identity
and ownership evidence. This host does not infer such proof from naming,
matching only the five old contracts, or an empty list fabricated in a receipt.
Changing a recorded execution implementation while resources or effects remain
continues to fail closed.
