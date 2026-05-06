# Meraki Action Batch Limits

This document explains the limits Meraki enforces on action batches and how merakiops handles them.

---

## Limits

| Batch type | Max actions per batch | API blocks until complete? |
|---|---|---|
| Asynchronous (`synchronous=False`) | 100 | No |
| Synchronous (`synchronous=True`) | 20 | Yes |

---

## Automatic splitting

`ActionBatch.from_actions()` splits your list of actions into chunks that fit within these limits. You never need to count or split manually.

```python
# 250 actions, async — produces 3 batches: [100, 100, 50]
batches = ActionBatch.from_actions(actions, organization_id="123456")

# 50 actions, synchronous — produces 3 batches: [20, 20, 10]
batches = ActionBatch.from_actions(
    actions,
    organization_id="123456",
    synchronous=True,
    confirmed=True,  # synchronous batches must be confirmed immediately
)
```

---

## Asynchronous vs synchronous

### Asynchronous (default)

- Up to 100 actions per batch
- `create()` returns immediately after Meraki accepts the batch
- The batch executes in the background on Meraki's side
- Call `status()` or `verify()` to check results
- Best for large-scale changes where you don't need to block

### Synchronous

- Up to 20 actions per batch
- `create()` blocks until all actions in the batch have completed
- Useful when you need to confirm success before proceeding to the next step
- `confirmed=True` is required — synchronous batches must be confirmed on submission

---

## Rate limiting

Meraki's API has rate limits. `create()` sleeps for 5 seconds after each batch submission by default to avoid hitting these limits when looping over many batches.

```python
# Default: sleep 5 seconds between batches
for batch in batches:
    batch.create()

# Custom: sleep 10 seconds
for batch in batches:
    batch.create(sleep_seconds=10)

# Disable: no sleep (use only if you are certain you won't hit rate limits)
for batch in batches:
    batch.create(sleep_seconds=0)
```

---

## confirmed=False vs confirmed=True

When `confirmed=False` (the default), a batch is created in Meraki's system as a **preview** and does not execute until you call `confirm()`. This gives you a chance to review before any changes are made.

When `confirmed=True`, the batch is queued for execution immediately on `create()`.

```python
# Safe workflow: preview first
batches = ActionBatch.from_actions(actions, organization_id="123456", confirmed=False)
for batch in batches:
    batch.create()    # batch exists in Meraki but does not run yet
    # ... review batch.id, inspect actions ...
    batch.confirm()   # now it runs

# Immediate workflow: execute on create()
batches = ActionBatch.from_actions(actions, organization_id="123456", confirmed=True)
for batch in batches:
    batch.create()    # executes immediately
```

---

## Meraki API reference

- Create batch: `POST /organizations/{organizationId}/actionBatches`
- Confirm batch: `PUT /organizations/{organizationId}/actionBatches/{actionBatchId}`
- Check status: `GET /organizations/{organizationId}/actionBatches/{actionBatchId}`

Full reference: https://developer.cisco.com/meraki/api-v1/create-organization-action-batch/
