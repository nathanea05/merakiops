# merakiops

[![PyPI - Version](https://img.shields.io/pypi/v/merakiops.svg)](https://pypi.org/project/merakiops)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/merakiops.svg)](https://pypi.org/project/merakiops)

A Python library for creating, submitting, and verifying [Meraki](https://meraki.cisco.com/) API action batches.

Built on top of [merakisync](https://github.com/nathanea05/merakisync), merakiops lets you apply configuration changes across thousands of Meraki networks reliably and at scale.

---

## What it does

merakiops provides two classes:

- **`Action`** — wraps a single Meraki API change (update, create, or destroy) on a typed `merakisync` model object.
- **`ActionBatch`** — groups Actions into Meraki API action batches, handles the 100-action limit automatically, submits them to Meraki, and verifies the results.

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

# 4. Build batches — automatically split at 100 actions each
batches = ActionBatch.from_actions(
    actions,
    org_id="123456",
    confirmed=False,     # does not execute until confirm() is called
    synchronous=False,
)

# 5. Submit, wait, and verify in one call
result = ActionBatch.run(actions, org_id="123456")

print(f"Verified:     {len(result.verified)}")
print(f"Mismatched:   {len(result.mismatched)}")
print(f"Unverifiable: {len(result.unverifiable)}")
```

---

## Actions

Each `Action` corresponds to a single API call within a batch. Always use the factory classmethods.

### update — modify an existing resource

Only the fields that changed are included in the request body.

```python
from merakiops import Action

port.vlan = 200
port.name = "uplink"
action = Action.update(port)
```

### create — add a new resource

All fields from the object are included. The request goes to the collection endpoint.

```python
from merakisync.models.vlan import Vlan

new_vlan = Vlan(network_id="N_123", vlan_id=200, name="Finance", ...)
action = Action.create(new_vlan)
```

### destroy — delete a resource

No body is sent. The resource is identified by its path.

```python
action = Action.destroy(old_vlan)
```

---

## ActionBatch

### from_actions() — create batches

Splits your actions into batches that respect Meraki's limits automatically.

```python
batches = ActionBatch.from_actions(
    actions,
    org_id="123456",
    confirmed=False,      # default — batches do not execute until confirm()
    synchronous=False,    # default — async execution
    callback=None,        # optional Meraki webhook callback config
)
```

| Mode | Max actions per batch |
|---|---|
| Asynchronous (`synchronous=False`) | 100 |
| Synchronous (`synchronous=True`) | 20 |

### create() — submit to Meraki

```python
batch.create()                  # sleeps 5 seconds after submission by default
batch.create(sleep_seconds=0)   # disable throttling
```

### confirm() — execute the batch

Only needed when the batch was created with `confirmed=False`.

```python
batch.confirm()
```

### status() — check completion

```python
status = batch.status()
# {"completed": True, "failed": False, "errors": []}
```

### `wait_for_all()` — wait for async batches to finish

For async batches, call this before `verify_many()`. `create()` returns immediately after Meraki accepts the batch — changes are not applied until the batch finishes executing.

```python
ActionBatch.wait_for_all(batches)                              # default 120s timeout
ActionBatch.wait_for_all(batches, timeout_seconds=300)         # custom timeout
ActionBatch.wait_for_all(batches, poll_interval=5.0)           # custom poll interval
```

Returns `{batch: bool}` — `True` if completed, `False` if failed.

### `wait_until_complete()` — wait for a single batch

```python
batch.wait_until_complete()
batch.wait_until_complete(timeout_seconds=60, poll_interval=2.0)
```

### `run()` — full lifecycle in one call (recommended)

Creates batches, submits, waits, and verifies in one call. Returns a single `VerifyResult`.

```python
result = ActionBatch.run(actions, org_id="123456")

print(result)  # VerifyResult(verified=98, mismatched=1, unverifiable=0, batch_errors=1)

for mismatch in result.mismatched:
    print(mismatch.action.resource, mismatch.mismatches)

# Retry pattern
remaining = initial_actions
for attempt in range(3):
    result = ActionBatch.run(remaining, org_id="123456")
    remaining = [m.action for m in result.mismatched]
    if not remaining:
        break
```

### `verify_many()` — check results across batches

Returns `{batch: VerifyResult}`. Preferred over `verify()` when verifying 10+ actions.

```python
results = ActionBatch.verify_many(batches)
for batch, result in results.items():
    print(f"Batch {batch.id}: {result}")
    for mismatch in result.mismatched:
        print(f"  {mismatch.action.resource}: {mismatch.mismatches}")
```

### `verify()` — single batch

```python
result = batch.verify()       # returns VerifyResult

result.verified               # list[Action] — all fields matched
result.mismatched             # list[Mismatch] — use .action and .mismatches
result.unverifiable           # list[Action] — could not be checked
result.batch_errors           # list[str] — Meraki execution errors
```

All verify methods use bulk fetching internally:

| Model | API calls regardless of action count |
|---|---|
| `Device`, `Network`, `Organization` | 1 per org |
| `Switchport` | 1 per unique serial |
| `Vlan`, `Ssid`, `L3FirewallRule`, `DhcpServerPolicy` | 1 per unique network |

---

## Batch limits

Meraki enforces the following limits on action batches:

| Limit | Value |
|---|---|
| Max actions per async batch | 100 |
| Max actions per synchronous batch | 20 |

`ActionBatch.from_actions()` handles splitting automatically. If you pass 250 actions, you get 3 batches (100, 100, 50). You never need to count or split manually.

See [docs/batch-limits.md](docs/batch-limits.md) for more detail.

---

## Full usage guide

See [docs/usage.md](docs/usage.md) for complete examples including:
- Updating switchport configurations across many devices
- Creating and destroying VLANs
- Verifying changes and handling mismatches
- Working with synchronous batches

---

## License

`merakiops` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
