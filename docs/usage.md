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

---

## Example 1 — Update switchport VLANs across many devices

**Goal:** Move all access ports currently on VLAN 100 to VLAN 200 across all switches in an org.

```python
from merakisync.models.switchport import Switchport
from merakiops import Action, ActionBatch

# 1. Fetch all switchports for a device from your merakisync database
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
    # 4. Build batches — from_actions() splits at 100 automatically
    batches = ActionBatch.from_actions(
        actions,
        organization_id="123456",
        confirmed=False,
    )

    # 5. Submit and confirm all batches
    for batch in batches:
        batch.create()     # submits to Meraki; sleeps 5s
        batch.confirm()    # queues for execution

    # 6. Wait for all batches to finish, then verify together
    ActionBatch.wait_for_all(batches)
    results = ActionBatch.verify_many(batches)
    for batch, result in results.items():
        print(f"Batch {batch.id}:")
        print(f"  Verified:     {len(result['verified'])}")
        print(f"  Mismatched:   {len(result['mismatched'])}")
        print(f"  Unverifiable: {len(result['unverifiable'])}")

        if result["mismatched"]:
            for item in result["mismatched"]:
                print(f"  ! {item['action'].resource}: {item['mismatches']}")
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

batches = ActionBatch.from_actions(actions, organization_id="123456")
for batch in batches:
    batch.create()
    batch.confirm()
    batch.status()
```

---

## Example 3 — Destroy a VLAN on multiple networks

**Goal:** Remove VLAN 999 from all networks where it exists.

```python
from merakisync.models.vlan import Vlan
from merakiops import Action, ActionBatch

# Fetch networks that have VLAN 999
vlans_to_remove = Vlan.get(organization_id="123456", source="database", vlan_id=999)
# Note: Vlan.get() requires network_id, not org_id — fetch per-network or use a list

actions = [Action.destroy(vlan) for vlan in vlans_to_remove]

if actions:
    batches = ActionBatch.from_actions(actions, organization_id="123456")
    for batch in batches:
        batch.create()
        batch.confirm()
        batch.status()
        result = batch.verify()
        # For destroy operations, verify() checks that the resource no longer exists.
        print(f"Destroyed and verified: {len(result['verified'])}")
```

---

## Example 4 — Update network settings

**Goal:** Enable a feature flag on a list of networks.

```python
from merakisync.models.network import Network
from merakiops import Action, ActionBatch

networks = Network.get(org_id="123456", source="database", tags_include=["automation"])

for net in networks:
    net.notes = "Managed by merakiops"

actions = [Action.update(net) for net in networks if net._changed_fields]
batches = ActionBatch.from_actions(actions, organization_id="123456")
for batch in batches:
    batch.create()
    batch.confirm()
```

---

## Verification

### verify_many() — preferred for 10+ actions

`ActionBatch.verify_many(batches)` verifies multiple batches together with the minimum possible number of API calls. It pools all actions across every batch before fetching, so each resource category is only fetched once regardless of how many batches reference it.

| Model | API calls regardless of action count |
|---|---|
| `Device`, `Network` | 1 per organization |
| `Switchport` | 1 per unique switch serial |
| `Vlan`, `Ssid`, `L3FirewallRule`, `DhcpServerPolicy` | 1 per unique network |

```python
results = ActionBatch.verify_many(batches)
for batch, result in results.items():
    print(f"Batch {batch.id}: {len(result['verified'])} verified, {len(result['mismatched'])} mismatched")
```

### verify() — single batch

`batch.verify()` applies the same bulk-fetch logic within one batch. Use it when you only have one batch, or need per-batch results without pooling across batches.

### Reading the result

`verify()` and `verify_many()` both return the same result structure:

```python
result = batch.verify()

# Actions where all body fields matched the live resource
for action in result["verified"]:
    print(f"OK: {action.resource}")

# Actions where one or more fields did not match
for item in result["mismatched"]:
    action = item["action"]
    for field, diff in item["mismatches"].items():
        print(
            f"MISMATCH: {action.resource} — "
            f"{field}: expected {diff['expected']!r}, got {diff['actual']!r}"
        )

# Actions that could not be checked
# (source_obj not stored, or model type not in the verify registry)
for action in result["unverifiable"]:
    print(f"UNVERIFIABLE: {action.resource}")
```

An action is "unverifiable" (not an error) when:
- It was constructed directly with `Action(...)` instead of a factory classmethod — no `source_obj` was stored
- The model type is not in the verify registry (currently: `UplinkUsage`, `Alert`, `Uplink`)

---

## Waiting for async batches to finish

For asynchronous batches (the default), `create()` returns as soon as Meraki accepts the batch — changes have not been applied yet. Calling `verify_many()` immediately will compare against the pre-change state and report everything as mismatched.

Always call `wait_for_all()` before `verify_many()`:

```python
for batch in batches:
    batch.create()
    batch.confirm()

# Waits until all batches complete or fail (timeout=120s by default)
ActionBatch.wait_for_all(batches)

# Now safe to verify — Meraki has finished applying all changes
results = ActionBatch.verify_many(batches)
```

For a single batch, use `wait_until_complete()`:

```python
batch.create()
batch.confirm()
batch.wait_until_complete()
result = batch.verify()
```

Custom timeout and poll interval:

```python
ActionBatch.wait_for_all(batches, timeout_seconds=300, poll_interval=5.0)
```

`wait_for_all()` returns `{batch: bool}` mapping each batch to `True` (completed) or `False` (failed). Check the return value if you need to handle failures before verifying:

```python
completion = ActionBatch.wait_for_all(batches)
failed_batches = [b for b, ok in completion.items() if not ok]
if failed_batches:
    for b in failed_batches:
        print(f"Batch {b.id} failed: {b.errors}")

results = ActionBatch.verify_many(batches)
```

**Synchronous batches** (`synchronous=True`) do not need this step — `create()` blocks until all actions complete. `wait_for_all()` is safe to call on them but returns immediately.

---

## Synchronous batches

For small sets of changes that must be confirmed before continuing:

```python
# Synchronous batches block until Meraki finishes all actions.
# Maximum 20 actions per batch. from_actions() splits automatically.
batches = ActionBatch.from_actions(
    actions,
    organization_id="123456",
    confirmed=True,       # must confirm immediately for synchronous
    synchronous=True,
)
for batch in batches:
    batch.create()        # blocks until batch completes

results = ActionBatch.verify_many(batches)
```

---

## Supported models

The following merakisync models are supported in `verify()`:

| Model | Notes |
|---|---|
| `Network` | Requires `organization_id` stored on the batch |
| `Device` | Requires `organization_id` stored on the batch |
| `Switchport` | Fetched by serial + port_id |
| `Vlan` | Fetched by network_id + vlan_id |
| `Ssid` | Fetched by network_id + number |
| `L3FirewallRule` | Fetched by network_id, matched by rule_order |
| `DhcpServerPolicy` | One per network; fetched by network_id |
| `Organization` | Filtered by org id from all organizations |

Models **not** in the verify registry (reported as "unverifiable"):
- `Uplink`, `UplinkUsage`, `Alert` — these are read-only or observational resources not modified via action batches
