# REVIEW.md — merakiops

Code review checklist for changes to merakiops.

---

## Action

- [ ] Created only via factory classmethods (`update`, `create`, `destroy`) — never `Action(...)` directly in user-facing code
- [ ] `body` is `None` for destroy operations — `to_meraki_action()` must omit the key entirely
- [ ] `operation` validated in `__post_init__` against `{"create", "update", "destroy"}`
- [ ] `resource` validated as a non-empty string in `__post_init__`
- [ ] `source_obj` stored when factory classmethods are used
- [ ] `frozen=True` — no fields mutated after creation
- [ ] `to_meraki_action()` returns a dict with only `resource`, `operation`, and optionally `body`
- [ ] No new fields added to `to_meraki_action()` output unless explicitly required by the Meraki API

## VerifyResult / Mismatch

- [ ] `VerifyResult` and `Mismatch` live in `result.py`, not `action_batch.py`
- [ ] Both are exported from `__init__.py`
- [ ] `VerifyResult` has `.verified`, `.mismatched`, `.unverifiable`, `.batch_errors`
- [ ] `VerifyResult.__repr__` shows counts, not full list contents
- [ ] `Mismatch` has `.action` and `.mismatches` (no string-keyed dict access)
- [ ] `batch_errors` is populated from `batch.errors` at `VerifyResult` construction time

## ActionBatch

- [ ] `run()` accepts `actions` (not batches) and returns a single combined `VerifyResult`
- [ ] `run()` calls `create()`, `confirm()`, `wait_for_all()`, `verify_many()` in that order
- [ ] `run()` passes all relevant kwargs (sleep_seconds, timeout_seconds, poll_interval, dashboard) through
- [ ] `verify()` returns `VerifyResult`, not a plain dict
- [ ] `verify_many()` returns `dict[ActionBatch, VerifyResult]`, not `dict[ActionBatch, dict]`
- [ ] Created via `ActionBatch.from_actions()` or `ActionBatch.run()` in all examples
- [ ] `from_actions()` raises `ValueError` for an empty actions list
- [ ] `from_actions()` splits at 100 actions for async, 20 for synchronous
- [ ] `create()` raises `RuntimeError` if `self.id` is already set
- [ ] `confirm()` raises `RuntimeError` if `self.id` is `None`
- [ ] `status()` raises `RuntimeError` if `self.id` is `None`
- [ ] `verify()` raises `RuntimeError` if `self.id` is `None`
- [ ] `create()` default `sleep_seconds=5.0` is present
- [ ] All API calls in `create()`, `confirm()`, `status()` use `get_dashboard()` when no `dashboard` arg is provided
- [ ] All API calls are wrapped in `try/except` with `logger.error(...)` before re-raise
- [ ] `verify()` handles `source_obj=None` as "unverifiable" — not an exception
- [ ] `verify()` handles unsupported model types as "unverifiable" — not an exception
- [ ] `verify()` correctly handles destroy: `current is None` → "verified", resource exists → "mismatched"
- [ ] `verify()` compares `action.body` keys (camelCase) against `current.to_meraki_dict()` output
- [ ] `errors`, `created_resources` initialized to `[]` in `__init__` (not class-level defaults)

## wait_until_complete / wait_for_all

- [ ] `wait_until_complete()` is a no-op for synchronous batches (returns immediately)
- [ ] `wait_until_complete()` raises `RuntimeError` if `self.id` is `None`
- [ ] `wait_until_complete()` raises `TimeoutError` (not `RuntimeError`) when deadline is exceeded
- [ ] `wait_until_complete()` respects remaining time on the final sleep (no over-sleeping past deadline)
- [ ] `wait_until_complete()` returns `not bool(self.errors)` — True = no errors, False = any action failed
- [ ] `wait_for_all()` raises `ValueError` for an empty list
- [ ] `wait_for_all()` raises `RuntimeError` if any batch is unsubmitted
- [ ] `wait_for_all()` raises `TimeoutError` listing incomplete batch IDs
- [ ] `wait_for_all()` continues polling remaining batches when one fails (does not stop early)
- [ ] `wait_for_all()` treats `completed=True OR failed=True` as done — both mean Meraki finished processing
- [ ] `wait_for_all()` logs "completed with N error(s)" for partial failures, not "failed"
- [ ] `wait_for_all()` skips synchronous and already-completed batches in the pending list
- [ ] `wait_for_all()` returns `{batch: not bool(batch.errors)}` — True = no errors, False = any action failed

## _bulk_fetch_model

- [ ] All model imports are deferred (inside the function body)
- [ ] Returns `(lookup, failed_pk_keys)` — a tuple, not just the lookup
- [ ] Org-level models (Device, Network, Organization): one call, all failures mark all source objs failed
- [ ] Serial-level (Switchport): grouped by serial, failure marks only that serial's objects failed
- [ ] Network-level (Vlan, Ssid, L3FirewallRule, DhcpServerPolicy): grouped by network_id, failure marks only that network's objects failed
- [ ] `DhcpServerPolicy` handled correctly — `get()` returns a single instance or `None`, not a list
- [ ] `L3FirewallRule` handled correctly — fetches all rules for the network, keyed by `_pk_key(rule)`
- [ ] `Organization` handled correctly — fetches all orgs, filters by `obj.id`

## General

- [ ] No new dependencies added without discussion
- [ ] No `async` code
- [ ] No silent exception swallowing — always log before re-raise
- [ ] All public methods have docstrings explaining arguments, return value, and exceptions raised
- [ ] Error messages tell the user what to do next, not just what failed
- [ ] `get_dashboard()` imported from `merakisync.dashboard`, not from `merakisync`
- [ ] Imports from merakisync use the submodule path (e.g., `from merakisync.dashboard import ...`)
- [ ] `__init__.py` exports `Action`, `ActionBatch`, `Mismatch`, and `VerifyResult`

## Meraki API compliance

- [ ] `update` actions use the single-resource endpoint from `obj.resource_path`
- [ ] `create` actions use the collection endpoint (last segment stripped from `resource_path`)
- [ ] `destroy` actions use the single-resource endpoint; body is `None`
- [ ] `confirmed=False` default on `ActionBatch.from_actions()`
- [ ] `synchronous=False` default on `ActionBatch.from_actions()`
- [ ] `createOrganizationActionBatch` used in `create()`
- [ ] `updateOrganizationActionBatch` used in `confirm()`
- [ ] `getOrganizationActionBatch` used in `status()`

## Documentation

- [ ] `README.md` updated to reflect any public API changes
- [ ] `docs/usage.md` updated with examples if behavior changed
- [ ] `CLAUDE.md` supported models list updated if verify registry changed
