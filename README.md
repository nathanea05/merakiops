# merakiops

[![PyPI - Version](https://img.shields.io/pypi/v/merakiops.svg)](https://pypi.org/project/merakiops)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/merakiops.svg)](https://pypi.org/project/merakiops)

A Python library for creating, submitting, and verifying [Meraki](https://meraki.cisco.com/) API action batches.

Built on top of [merakisync](https://github.com/nathanea05/merakisync), merakiops lets you apply configuration changes across thousands of Meraki networks reliably and at scale.

---

## What it does

merakiops provides two classes:

- **`Action`** — wraps a single Meraki API change (update, create, or destroy) on a typed `merakisync` model object.
- **`ActionBatch`** — groups Actions into Meraki API action batches, handles splitting automatically, submits them to Meraki, waits for completion, and verifies the results.

It also provides two result types:

- **`VerifyResult`** — the outcome of a verify operation: verified, mismatched, unverifiable, and batch errors.
- **`Mismatch`** — a single action whose live state did not match what was intended.

---

## Requirements

- Python 3.10+
- [merakisync](https://github.com/nathanea05/merakisync) installed and configured

---

## Installation

```console
pip install merakiops
```

---

## Quick start

```python
from merakisync.models.switchport import Switchport
from merakiops import Action, ActionBatch

# 1. Fetch current state from your merakisync database
ports = Switchport.get(serial="Q2AB-1234-5678", source="database")

# 2. Modify the objects you want to change
for port in ports:
    if port.vlan == 100:
        port.vlan = 200

# 3. Create Actions from the changed objects
actions = [Action.update(port) for port in ports if port._changed_fields]

# 4. Submit, wait, and verify — all in one call
result = ActionBatch.run("123456", actions)

print(f"Verified:     {len(result.verified)}")
print(f"Mismatched:   {len(result.mismatched)}")
print(f"Unverifiable: {len(result.unverifiable)}")
```

---

## Actions

Each `Action` corresponds to a single API call within a batch. Always use the factory classmethods — never instantiate `Action` directly.

### update — modify an existing resource

Only the fields that changed are included in the request body. Change tracking is automatic via `_changed_fields`.

```python
from merakiops import Action

port.vlan = 200
port.name = "uplink"
action = Action.update(port)
```

Use `fields_to_update` to explicitly specify fields instead of relying on change tracking. This is useful when restoring an object from a historical database record — the object holds the desired values but no fields have been mutated in the current session.

```python
old_port = Switchport.get(serial="Q2AB", source="database", ts=restore_time)[0]
action = Action.update(old_port, fields_to_update=["stp_guard", "vlan"])
```

`Action.update()` raises `ValueError` if there are no fields to include in the body.

### create — add a new resource

All fields from the object are included. The request goes to the collection endpoint (derived automatically by stripping the last path segment from `obj.resource_path`).

```python
from merakisync.models.vlan import Vlan

new_vlan = Vlan(network_id="N_123", vlan_id=200, name="Finance", ...)
action = Action.create(new_vlan)
```

`L3FirewallRule` and `DhcpServerPolicy` do not support create — use `Action.update()` for both.

### destroy — delete a resource

No body is sent. The resource is identified by its path alone.

```python
action = Action.destroy(old_vlan)
```

---

## ActionBatch

### from_actions() — create batches

Splits your actions into batches that respect Meraki's limits automatically.

```python
batches = ActionBatch.from_actions(
    "123456",
    actions,
    confirmed=False,      # default — batches do not execute until confirm()
    synchronous=False,    # default — async execution
    callback=None,        # optional Meraki webhook callback config
)
```

| Mode | Max actions per batch |
|---|---|
| Asynchronous (`synchronous=False`) | 100 |
| Synchronous (`synchronous=True`) | 20 |

`from_actions()` raises `ValueError` if `actions` is empty.

### create() — submit to Meraki

Submits the batch to the Meraki API and populates `batch.id`. Sleeps 5 seconds after submission by default to avoid rate limiting when looping over many batches.

```python
batch.create()                  # sleeps 5 seconds after submission
batch.create(sleep_seconds=0)   # disable sleep
```

Raises `RuntimeError` if the batch has already been submitted.

### confirm() — execute the batch

Only needed when the batch was created with `confirmed=False`.

```python
batch.confirm()
```

Has no effect if the batch is already confirmed. Raises `RuntimeError` if the batch has not been submitted yet.

### status() — check completion

Fetches the current status from Meraki and updates `batch.completed`, `batch.failed`, and `batch.errors`.

```python
status = batch.status()
# {"completed": True, "failed": False, "errors": []}
```

Raises `RuntimeError` if the batch has not been submitted yet.

### `wait_until_complete()` — wait for a single batch

For async batches, `create()` returns immediately after Meraki accepts the batch — changes are not applied yet. Call `wait_until_complete()` before `verify()` to avoid comparing against pre-change state.

```python
batch.wait_until_complete()
batch.wait_until_complete(timeout_seconds=60, poll_interval=2.0)
```

Returns `True` if no action errors, `False` if any action failed. Raises `TimeoutError` if the batch does not finish within `timeout_seconds`. No-op for synchronous batches.

### `wait_for_all()` — wait for multiple batches together

Polls all pending batches each interval and removes them from the wait list as they finish. Preferred over calling `wait_until_complete()` individually when working with multiple batches.

```python
ActionBatch.wait_for_all(batches)                              # default 120s timeout
ActionBatch.wait_for_all(batches, timeout_seconds=300)
ActionBatch.wait_for_all(batches, poll_interval=5.0)
```

Returns `{batch: bool}` — `True` if completed with no errors, `False` if any action failed. Synchronous batches are skipped (they complete before `create()` returns).

### `run()` — full lifecycle in one call (recommended)

Creates batches, submits, confirms, waits, and verifies in one call. Returns a single `VerifyResult` combining all batches.

```python
result = ActionBatch.run("123456", actions)

print(result)  # VerifyResult(verified=98, mismatched=1, unverifiable=0, batch_errors=1)

for mismatch in result.mismatched:
    print(mismatch.action.resource, mismatch.mismatches)
```

`run()` always calls `confirm()` after `create()` regardless of the `confirmed` setting, so batches will always execute.

**Retry pattern:**

```python
remaining = initial_actions
for attempt in range(3):
    result = ActionBatch.run("123456", remaining)
    remaining = [m.action for m in result.mismatched]
    if not remaining:
        break
```

### `verify_many()` — check results across batches

Returns `{batch: VerifyResult}`. Preferred over `verify()` when verifying 10+ actions — pools resource fetches across all batches to minimize API calls.

```python
results = ActionBatch.verify_many(batches)
for batch, result in results.items():
    print(f"Batch {batch.id}: {result}")
    for mismatch in result.mismatched:
        print(f"  {mismatch.action.resource}: {mismatch.mismatches}")
```

Raises `ValueError` if `batches` is empty, `RuntimeError` if any batch has not been submitted.

### `verify()` — single batch

```python
result = batch.verify()       # returns VerifyResult

result.verified               # list[Action] — all fields matched
result.mismatched             # list[Mismatch] — use .action and .mismatches
result.unverifiable           # list[Action] — could not be checked
result.batch_errors           # list[str] — Meraki execution errors
```

Raises `RuntimeError` if the batch has not been submitted yet.

All verify methods use bulk fetching internally:

| Model | API calls regardless of action count |
|---|---|
| `Device`, `Network`, `Organization` | 1 per org |
| `Switchport` | 1 per unique serial |
| `Vlan`, `Ssid`, `L3FirewallRule`, `DhcpServerPolicy` | 1 per unique network |

---

## VerifyResult

All verify methods return a `VerifyResult`:

```python
result.verified      # list[Action]   — all body fields matched live state
result.mismatched    # list[Mismatch] — one or more fields did not match
result.unverifiable  # list[Action]   — could not be checked (see below)
result.batch_errors  # list[str]      — Meraki execution errors from batch.errors
```

Each `Mismatch` has `.action` and `.mismatches`:

```python
for mismatch in result.mismatched:
    print(mismatch.action.resource)
    for field, diff in mismatch.mismatches.items():
        print(f"  {field}: expected {diff['expected']!r}, got {diff['actual']!r}")
```

**Unverifiable** means the action could not be checked — not that it failed. An action is unverifiable when:
- `source_obj` was not stored on the Action
- The model type is not in the verify registry
- An API error occurred while fetching the resource group

**`batch_errors`** are error strings returned by Meraki for actions that failed during batch execution. These come from `batch.errors` and are independent of the field-level comparison in `mismatched`.

---

## Manual lifecycle

For cases where you need per-batch control or visibility:

```python
batches = ActionBatch.from_actions("123456", actions)

for batch in batches:
    batch.create()     # submit; sleeps 5s by default
    batch.confirm()    # queue for execution

ActionBatch.wait_for_all(batches)        # poll until all finish
results = ActionBatch.verify_many(batches)

for batch, result in results.items():
    print(f"Batch {batch.id}: {result}")
    if result.batch_errors:
        print("  Errors:", result.batch_errors)
    for mismatch in result.mismatched:
        print(f"  {mismatch.action.resource}: {mismatch.mismatches}")
```

---

## Synchronous batches

For small sets of changes where you need the batch to complete before continuing. Meraki requires `confirmed=True` for synchronous batches.

```python
result = ActionBatch.run(
    "123456",
    actions,
    confirmed=True,       # required by Meraki for synchronous batches
    synchronous=True,     # up to 20 actions per batch; from_actions() splits automatically
)
```

`wait_for_all()` and `wait_until_complete()` are no-ops for synchronous batches — `create()` blocks until all actions complete.

---

## Supported models

The following merakisync models are supported in all verify methods:

| Model | Notes |
|---|---|
| `Network` | Fetched org-wide; 1 API call |
| `Device` | Fetched org-wide; 1 API call |
| `Organization` | Fetched globally; 1 API call |
| `Switchport` | 1 API call per unique switch serial |
| `Vlan` | 1 API call per unique network |
| `Ssid` | 1 API call per unique network |
| `L3FirewallRule` | 1 API call per unique network |
| `DhcpServerPolicy` | 1 API call per unique network |

Actions for unsupported model types are reported as `unverifiable` — not as errors.

---

## Batch limits

| Limit | Value |
|---|---|
| Max actions per async batch | 100 |
| Max actions per synchronous batch | 20 |

`ActionBatch.from_actions()` handles splitting automatically. If you pass 250 async actions, you get 3 batches (100, 100, 50). You never need to count or split manually.

See [docs/batch-limits.md](docs/batch-limits.md) for more detail.

---

## Full usage guide

See [docs/usage.md](docs/usage.md) for complete examples including:
- Updating switchport configurations across many devices
- Creating and destroying VLANs
- Verifying changes and handling mismatches
- Retry patterns for mismatched actions
- Working with synchronous batches

---

## License

`merakiops` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
