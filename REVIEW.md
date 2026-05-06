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

## ActionBatch

- [ ] Created via `ActionBatch.from_actions()` in all examples and documentation
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

## _fetch_current_state

- [ ] All model imports are deferred (inside the function body)
- [ ] Returns `(True, obj)` for a found resource, `(True, None)` for not found, `(False, None)` for unsupported
- [ ] `DhcpServerPolicy` handled correctly — `get()` returns a single instance or `None`, not a list
- [ ] `L3FirewallRule` handled correctly — fetches all rules, filters by `rule_order`
- [ ] `Organization` handled correctly — fetches all orgs, filters by `obj.id`

## General

- [ ] No new dependencies added without discussion
- [ ] No `async` code
- [ ] No silent exception swallowing — always log before re-raise
- [ ] All public methods have docstrings explaining arguments, return value, and exceptions raised
- [ ] Error messages tell the user what to do next, not just what failed
- [ ] `get_dashboard()` imported from `merakisync.dashboard`, not from `merakisync`
- [ ] Imports from merakisync use the submodule path (e.g., `from merakisync.dashboard import ...`)
- [ ] `__init__.py` exports `Action` and `ActionBatch` only

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
