# Usage Guide — merakiops

This guide walks through real-world usage patterns for merakiops with complete examples.

---

## Prerequisites

merakiops requires [merakisync](https://github.com/nathanea05/merakisync) to be installed and configured with a valid Meraki API key and PostgreSQL database.

```console
pip install merakiops merakisync
```

---

## Concepts

### Action

An `Action` represents a single API change — one resource, one operation. You never create an `Action` with its constructor directly. Instead, use the factory classmethods:

| Method | When to use |
|---|---|
| `Action.update(obj)` | Modify fields on an existing resource |
| `Action.create(obj)` | Create a new resource |
| `Action.destroy(obj)` | Delete a resource |

### ActionBatch

An `ActionBatch` is a group of Actions submitted together to the Meraki API. One batch can hold up to 100 actions (async) or 20 actions (synchronous). `ActionBatch.from_actions()` handles splitting automatically.

### VerifyResult

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

---

## Example 1 — Update switchport VLANs across many devices

**Goal:** Move all access ports currently on VLAN 100 to VLAN 200 across all switches in an org.

```python
from merakisync.models.switchport import Switchport
from merakiops import Action, ActionBatch

# 1. Fetch switchports from your merakisync database
ports = Switchport.get(serial="Q2AB-1234-5678", source="database")
# Repeat for however many devices you have, or loop over a device list.

# 2. Modify the objects that need to change
for port in ports:
    if port.vlan == 100 and port.type == "access":
        port.vlan = 200

# 3. Build Actions only for ports that were modified
actions = [Action.update(port) for port in ports if port._changed_fields]

if not actions:
    print("No changes to apply.")
else:
    # 4. Submit, wait, and verify in one call
    result = ActionBatch.run(actions, org_id="123456")

    print(f"Verified:     {len(result.verified)}")
    print(f"Mismatched:   {len(result.mismatched)}")
    print(f"Unverifiable: {len(result.unverifiable)}")

    for mismatch in result.mismatched:
        print(f"  ! {mismatch.action.resource}: {mismatch.mismatches}")
```

---

## Example 2 — Create a VLAN on multiple networks

**Goal:** Add VLAN 300 "Cameras" to a list of networks.

```python
from merakisync.models.vlan import Vlan
from merakiops import Action, ActionBatch

network_ids = ["N_aaa", "N_bbb", "N_ccc"]

actions = []
for network_id in network_ids:
    new_vlan = Vlan(
        network_id=network_id,
        vlan_id=300,
        name="Cameras",
        subnet="10.30.0.0/24",
        appliance_ip="10.30.0.1",
    )
    actions.append(Action.create(new_vlan))

result = ActionBatch.run(actions, org_id="123456")
print(result)
```

---

## Example 3 — Destroy a VLAN on multiple networks

**Goal:** Remove VLAN 999 from all networks where it exists.

```python
from merakisync.models.vlan import Vlan
from merakiops import Action, ActionBatch

vlans_to_remove = Vlan.get(network_id="N_abc", source="database", vlan_id=999)
actions = [Action.destroy(vlan) for vlan in vlans_to_remove]

if actions:
    result = ActionBatch.run(actions, org_id="123456")
    # For destroy operations, verify() checks that the resource no longer exists.
    print(f"Destroyed and verified: {len(result.verified)}")
```

---

## Example 4 — Update network settings

**Goal:** Tag a list of networks as managed.

```python
from merakisync.models.network import Network
from merakiops import Action, ActionBatch

networks = Network.get(org_id="123456", source="database", tags_include=["automation"])

for net in networks:
    net.notes = "Managed by merakiops"

actions = [Action.update(net) for net in networks if net._changed_fields]
result = ActionBatch.run(actions, org_id="123456")
print(result)
```

---

## Example 5 — Retry mismatched actions

Use `result.mismatched` to drive retries. Always set a maximum number of attempts to avoid infinite loops on permanent failures (e.g., a port type that rejects a specific config change).

```python
from merakiops import Action, ActionBatch

remaining = initial_actions
max_retries = 3

for attempt in range(max_retries):
    result = ActionBatch.run(remaining, org_id="123456")

    print(f"Attempt {attempt + 1}: {len(result.verified)} verified, {len(result.mismatched)} mismatched")

    remaining = [m.action for m in result.mismatched]
    if not remaining:
        break
else:
    print(f"Gave up after {max_retries} attempts. Still mismatched:")
    for mismatch in result.mismatched:
        print(f"  {mismatch.action.resource}: {mismatch.mismatches}")
```

---

## Verification

### run() — full lifecycle (recommended)

`ActionBatch.run()` handles create → confirm → wait → verify in one call and returns a single `VerifyResult` combining results across all batches.

```python
result = ActionBatch.run(
    actions,
    org_id="123456",
    confirmed=False,       # default
    synchronous=False,     # default
    timeout_seconds=120,   # default
    poll_interval=3.0,     # default
    sleep_seconds=5.0,     # default sleep between batch submissions
)
```

### verify_many() — per-batch results

Returns `{batch: VerifyResult}`. Use this instead of `run()` when you need per-batch visibility or are managing the submit/wait lifecycle yourself.

```python
for batch in batches:
    batch.create()
    batch.confirm()

ActionBatch.wait_for_all(batches)
results = ActionBatch.verify_many(batches)

for batch, result in results.items():
    print(f"Batch {batch.id}: {result}")
    for mismatch in result.mismatched:
        print(f"  {mismatch.action.resource}: {mismatch.mismatches}")
```

### verify() — single batch

```python
result = batch.verify()   # returns VerifyResult
```

### API call counts

All verify methods use bulk fetching:

| Model | API calls regardless of action count |
|---|---|
| `Device`, `Network`, `Organization` | 1 per org |
| `Switchport` | 1 per unique serial |
| `Vlan`, `Ssid`, `L3FirewallRule`, `DhcpServerPolicy` | 1 per unique network |

---

## Waiting for async batches to finish

For asynchronous batches (the default), `create()` returns as soon as Meraki accepts the batch — changes have not been applied yet. `run()` handles this automatically. If you are managing the lifecycle yourself with `verify_many()`, always call `wait_for_all()` first:

```python
for batch in batches:
    batch.create()
    batch.confirm()

ActionBatch.wait_for_all(batches)           # default 120s timeout
results = ActionBatch.verify_many(batches)
```

`wait_for_all()` returns `{batch: bool}` — `True` if no action errors, `False` if any action failed:

```python
completion = ActionBatch.wait_for_all(batches)
for batch, ok in completion.items():
    if not ok:
        print(f"Batch {batch.id} had errors: {batch.errors}")
```

**Synchronous batches** (`synchronous=True`) do not need this — `create()` blocks until all actions complete.

---

## Synchronous batches

For small sets of changes that must complete before continuing:

```python
# Maximum 20 actions per batch. from_actions() splits automatically.
result = ActionBatch.run(
    actions,
    org_id="123456",
    confirmed=True,       # must be True for synchronous
    synchronous=True,
)
```

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
| `L3FirewallRule` | 1 API call per unique network, matched by rule_order |
| `DhcpServerPolicy` | 1 API call per unique network |

Models reported as "unverifiable" (read-only or observational):
- `Uplink`, `UplinkUsage`, `Alert`
