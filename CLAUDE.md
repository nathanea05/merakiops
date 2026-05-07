# CLAUDE.md — merakiops

---

## Purpose

You are assisting in building **merakiops**: a Python library for creating, submitting, and verifying Meraki API action batches.

merakiops is built on top of **merakisync** and works with its typed model objects (`MerakiObj` subclasses). A merakisync object — a `Switchport`, `Vlan`, `Network`, etc. — is the starting point for every Action.

This library has exactly one purpose: **make it safe and reliable to batch configuration changes across thousands of Meraki networks.**

---

## Developer context

The developer is a network engineer managing hundreds of Meraki networks. They:

- Value reliability and correctness above all else.
- Will hand this library to junior engineers and network engineers with minimal Python experience.
- Expect every function, parameter, and error message to be self-explanatory without reading source code.
- Want to be able to push changes to thousands of networks and verify the results programmatically.

---

## Guiding philosophy

### 1. UNIX philosophy — do one thing well

This project handles exactly one concern: create, send, and verify Meraki action batches. Do not add scheduling, retry loops, reporting, alerting, or workflow orchestration. Those belong in the calling application.

### 2. Readable over clever

This library will be used by people with minimal Python experience. Prioritize clarity at every level:

- Simple > abstraction
- Explicit > implicit
- Named keyword arguments over positional where ambiguity exists
- Error messages that explain what to do, not just what went wrong
- No magic — no `__init_subclass__`, dynamic class factories, or metaprogramming

### 3. Production mindset

Assume thousands of networks. Failures must be:
- Logged before they propagate (log and re-raise; never swallow silently)
- Distinguishable — which batch? which action? which resource?
- Debuggable without a debugger attached

### 4. Safety by default

Action batches modify live production network infrastructure. Every default should minimize risk:

- `confirmed=False` by default — batches do not execute until `confirm()` is called
- `synchronous=False` by default
- No silent retries
- Explicit errors when a batch has already been submitted

---

## Project structure

```
src/merakiops/
├── __init__.py      Public re-exports: Action and ActionBatch only.
├── __about__.py     Version string only.
├── action.py        Action class. One action = one API call within a batch.
└── action_batch.py  ActionBatch class. Submission, confirmation, status, verification.
```

---

## Data flow

```
MerakiObj (from merakisync)
  │
  └─► Action.update(obj)        # changed fields only, single-resource path
      Action.create(obj)        # all fields, collection path
      Action.destroy(obj)       # no body, single-resource path
            │
            └─► ActionBatch.from_actions(actions, org_id)
                      │
                      └─► batch.create()     # POST /organizations/{id}/actionBatches
                          batch.confirm()    # PUT  /organizations/{id}/actionBatches/{id}
                          batch.status()     # GET  /organizations/{id}/actionBatches/{id}
                          batch.verify()     # model.get(source="meraki") + field comparison
```

No raw dicts passed to callers. No API calls outside the ActionBatch methods.

---

## Action

### Rules

- Always created from a factory classmethod: `Action.update()`, `Action.create()`, `Action.destroy()`
- `frozen=True` — an Action must not change after creation
- `source_obj` is always stored when using factory classmethods — required for `verify()`
- `body` is `None` for destroy operations; `to_meraki_action()` omits the key entirely

### Valid operations

| Operation | When to use | Resource path | Body |
|---|---|---|---|
| `update` | Modify fields on an existing resource | Single-resource path | Changed fields only (camelCase) |
| `create` | Create a new resource | Collection path | All fields (camelCase) |
| `destroy` | Delete a resource | Single-resource path | None (omitted) |

### create() and resource paths

`Action.create()` derives the collection path by stripping the last segment from `obj.resource_path`. This works for: `Network`, `Device`, `Switchport`, `Vlan`, `Ssid`, `Organization`.

`L3FirewallRule` and `DhcpServerPolicy` do not support create — use `Action.update()` for both. Their `resource_path` is already the collection/single endpoint used for updates.

---

## ActionBatch

### Rules

- Always created via `ActionBatch.from_actions()`, never instantiated directly
- Splits actions automatically: 100 actions per batch (async) or 20 (synchronous)
- `confirmed=False` default — batches do not execute until `confirm()` is called
- `synchronous=False` default
- `create()` sleeps 5 seconds after submission by default (pass `sleep_seconds=0` to disable)
- Each batch instance can only be submitted once — `create()` raises `RuntimeError` if `id` is set
- All methods that call the Meraki API accept an optional `dashboard` argument

### Method lifecycle

```
ActionBatch.from_actions()         — creates batch objects; splits if needed
    │
    └─► create()                   — submits to Meraki; sets self.id; sleeps 5s
    └─► confirm()                  — executes the batch (only needed if confirmed=False)
    └─► wait_until_complete()      — polls until completed/failed (async batches only)
    └─► status()                   — single poll of current batch state
    └─► verify()                   — compares live resource state against action.body

ActionBatch.wait_for_all(batches)  — polls all batches together until all finish
ActionBatch.verify_many(batches)   — verifies all batches with minimal API calls
```

### Async vs synchronous timing

For **async batches** (`synchronous=False`, the default), `create()` returns as soon as Meraki accepts the batch. Changes have not been applied yet. Always call `wait_until_complete()` or `wait_for_all()` before `verify()` or `verify_many()`, otherwise verify will compare against the pre-change state and report everything as mismatched.

For **synchronous batches** (`synchronous=True`), `create()` blocks until all actions complete. `wait_until_complete()` and `wait_for_all()` are no-ops for these batches.

### verify() and verify_many() behavior

- Uses bulk fetching: 1 API call per org (Device/Network), 1 per serial (Switchport), 1 per network (Vlan/Ssid/L3FirewallRule/DhcpServerPolicy)
- `verify_many()` is preferred for 10+ actions — pools fetches across all batches
- merakisync model.get() manages its own API connectivity; `verify()` accepts no `dashboard` arg
- Supported models: `Network`, `Device`, `Switchport`, `Vlan`, `Ssid`, `L3FirewallRule`, `DhcpServerPolicy`, `Organization`
- Actions without `source_obj` → "unverifiable" (not an error)
- Unsupported model types → "unverifiable" (not an error)
- API fetch errors for a group → all actions in that group become "unverifiable"
- Destroy operations: resource not found → "verified"; resource still exists → "mismatched"
- `verify()` returns `{"verified": [...], "mismatched": [...], "unverifiable": [...]}`
- `verify_many()` returns `{batch: {"verified": [...], "mismatched": [...], "unverifiable": [...]}}`

---

## Adding a model to the verify registry

When merakisync adds a new model that supports action batch operations:

1. Add a deferred import inside `_fetch_current_state()` in `action_batch.py`
2. Add an `elif cls is NewModel:` branch that calls `NewModel.get(source="meraki", ...)`
3. Return `True, result` (supported) or `True, None` (supported but not found)
4. Update the supported models list in this file, `README.md`, and `docs/usage.md`

---

## Import rules

- merakiops imports from merakisync's submodules directly, not from the merakisync package root
  ```python
  # Correct
  from merakisync.dashboard import get_dashboard

  # Wrong — may cause circular imports in some environments
  from merakisync import get_dashboard
  ```
- All Meraki API calls in `create()`, `confirm()`, `status()` go through `get_dashboard()`
- Model imports in `_fetch_current_state()` are deferred (inside the function body)
- `get_dashboard()` is imported inside the method body where it is used, not at the top of the file

---

## Meraki API limits

| Batch type | Max actions per batch |
|---|---|
| Asynchronous (`synchronous=False`) | 100 |
| Synchronous (`synchronous=True`) | 20 |

`from_actions()` handles splitting. You do not need to count actions manually.

---

## Before writing code

Always:
1. Explain what you plan to change and why.
2. Identify any risks or places where existing behavior could break.
3. Call out anything unclear before starting.
4. Check existing patterns in both merakiops and merakisync before inventing new ones.
5. Confirm alignment with the developer before making any changes.

---

## Definition of done

A change is complete when:
- It works correctly for the intended use case
- Error messages are clear and actionable — they tell the user what to do
- All public methods have docstrings that explain the return value and any exceptions raised
- Edge cases are handled: empty actions list, already-submitted batch, `None` source_obj
- `README.md` and `docs/usage.md` reflect any changes to the public API

---

## What not to do

- Do not add scheduling, retry logic, or workflow orchestration
- Do not add `async` code
- Do not introduce new dependencies without discussion
- Do not silently swallow exceptions — log and re-raise
- Do not make Meraki API calls outside of `create()`, `confirm()`, `status()`, and `verify()`
- Do not accept `source_obj=None` as valid for any factory classmethod
- Do not call `batch.create()` twice on the same instance
- Do not submit a batch with 0 actions
- Do not put credentials in source code
- Do not import from `merakisync` (the package root) inside method bodies — import from the submodule directly

---

## Reference

- Batch limits and chunking: `docs/batch-limits.md`
- Full usage guide with examples: `docs/usage.md`
- Meraki action batch API: https://developer.cisco.com/meraki/api-v1/create-organization-action-batch/
