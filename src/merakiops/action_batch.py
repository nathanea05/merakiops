from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from merakiops.action import Action
from merakiops.result import Mismatch, VerifyResult

if TYPE_CHECKING:
    from merakisync.models.base import MerakiObj

logger = logging.getLogger(__name__)

MAX_ACTIONS_PER_BATCH = 100
MAX_SYNCHRONOUS_ACTIONS = 20


class ActionBatch:
    """A batch of Actions to be submitted to the Meraki API.

    Do not instantiate directly. Use ActionBatch.from_actions() to create
    one or more batches from a list of Action objects. from_actions() handles
    splitting automatically when you have more than 100 actions.

    Meraki API reference:
        https://developer.cisco.com/meraki/api-v1/create-organization-action-batch/

    Example:
        actions = [Action.update(port) for port in changed_ports]
        batches = ActionBatch.from_actions(
            actions,
            org_id="123456",
            confirmed=False,
        )
        for batch in batches:
            batch.create()
            batch.confirm()
            batch.status()

        # Verify all batches together with minimal API calls
        results = ActionBatch.verify_many(batches)
        for batch, result in results.items():
            print(f"Batch {batch.id}: {len(result['verified'])} verified")
    """

    def __init__(
        self,
        org_id: str,
        actions: list[Action],
        *,
        confirmed: bool = False,
        synchronous: bool = False,
        callback: dict | None = None,
    ) -> None:
        self.org_id = org_id
        self.actions = actions
        self.confirmed = confirmed
        self.synchronous = synchronous
        self.callback = callback

        # Populated after create() is called.
        self.id: str | None = None
        self.completed: bool = False
        self.failed: bool = False
        self.errors: list[str] = []
        self.created_resources: list[dict] = []

    def __repr__(self) -> str:
        if self.id is None:
            status = "not submitted"
        elif self.failed:
            status = "failed"
        elif self.completed:
            status = "completed"
        else:
            status = f"submitted id={self.id!r}"
        return (
            f"ActionBatch("
            f"org_id={self.org_id!r}, "
            f"actions={len(self.actions)}, "
            f"synchronous={self.synchronous}, "
            f"confirmed={self.confirmed}, "
            f"status={status!r})"
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_actions(
        cls,
        org_id: str,
        actions: list[Action],
        *,
        confirmed: bool = False,
        synchronous: bool = False,
        callback: dict | None = None,
    ) -> list[ActionBatch]:
        """Create one or more ActionBatch objects from a list of Actions.

        Splits actions automatically into chunks that respect Meraki's limits:
          - Asynchronous batches (synchronous=False): up to 100 actions each
          - Synchronous batches (synchronous=True):   up to 20 actions each

        Args:
            actions:         List of Action objects to batch. Must not be empty.
            org_id: Meraki organization ID that owns these resources.
            confirmed:       Whether batches are confirmed immediately on create().
                             Default False — batches do not execute until confirm()
                             is called.
            synchronous:     Whether Meraki should execute the batch synchronously
                             (API call blocks until complete). Default False.
                             Synchronous batches are limited to 20 actions.
            callback:        Optional Meraki webhook callback config dict.

        Returns:
            A list of ActionBatch objects, one per chunk.

        Raises:
            ValueError: If actions is empty.

        Example:
            batches = ActionBatch.from_actions(
                actions,
                org_id="123456",
                confirmed=False,
            )
        """
        if not actions:
            raise ValueError("actions cannot be empty")

        batch_size = MAX_SYNCHRONOUS_ACTIONS if synchronous else MAX_ACTIONS_PER_BATCH

        batches = []
        for i in range(0, len(actions), batch_size):
            chunk = actions[i : i + batch_size]
            batches.append(
                cls(
                    org_id=org_id,
                    actions=chunk,
                    confirmed=confirmed,
                    synchronous=synchronous,
                    callback=callback,
                )
            )

        logger.debug(
            "Created %d batch(es) from %d action(s) for organization %s",
            len(batches),
            len(actions),
            org_id,
        )
        return batches

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    def create(self, dashboard=None, *, sleep_seconds: float = 5.0) -> ActionBatch:
        """Submit this batch to the Meraki API.

        Populates self.id, self.completed, self.failed, self.errors, and
        self.created_resources from the API response.

        Args:
            dashboard:     Optional DashboardAPI instance. If not provided,
                           get_dashboard() from merakisync is called.
            sleep_seconds: Seconds to sleep after submission to avoid rate
                           limiting when creating multiple batches in a loop.
                           Default 5.0. Set to 0 to disable.

        Returns:
            self, for method chaining.

        Raises:
            RuntimeError: If this batch has already been submitted (id is set).
        """
        if self.id is not None:
            raise RuntimeError(
                f"This batch has already been submitted (id={self.id!r}). "
                "Each ActionBatch can only be submitted once. "
                "Use ActionBatch.from_actions() to create a new batch."
            )

        if dashboard is None:
            from merakisync.dashboard import get_dashboard
            dashboard = get_dashboard()

        payload: dict = {
            "confirmed": self.confirmed,
            "synchronous": self.synchronous,
            "actions": [action.to_meraki_action() for action in self.actions],
        }
        if self.callback is not None:
            payload["callback"] = self.callback

        logger.info(
            "Submitting action batch of %d action(s) to organization %s "
            "(confirmed=%s, synchronous=%s)",
            len(self.actions),
            self.org_id,
            self.confirmed,
            self.synchronous,
        )

        try:
            response = dashboard.organizations.createOrganizationActionBatch(
                self.org_id,
                **payload,
            )
        except Exception as exc:
            logger.error(
                "Failed to submit action batch to organization %s: %s",
                self.org_id,
                exc,
            )
            raise

        self.id = response.get("id")
        status = response.get("status", {})
        self.completed = status.get("completed", False)
        self.failed = status.get("failed", False)
        self.errors = status.get("errors", [])
        self.created_resources = response.get("createdResources", [])

        logger.info("Batch submitted successfully (id=%s)", self.id)

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        return self

    def confirm(self, dashboard=None) -> ActionBatch:
        """Queue this batch for execution by marking it confirmed.

        Only needed when the batch was created with confirmed=False.
        Has no effect and returns immediately if the batch is already confirmed.

        Args:
            dashboard: Optional DashboardAPI instance. If not provided,
                       get_dashboard() from merakisync is called.

        Returns:
            self, for method chaining.

        Raises:
            RuntimeError: If the batch has not been submitted yet (id is None).
        """
        if self.id is None:
            raise RuntimeError(
                "This batch has not been submitted yet. Call create() first."
            )

        if self.confirmed:
            logger.debug("Batch %s is already confirmed — skipping", self.id)
            return self

        if dashboard is None:
            from merakisync.dashboard import get_dashboard
            dashboard = get_dashboard()

        logger.info("Confirming batch %s", self.id)

        try:
            response = dashboard.organizations.updateOrganizationActionBatch(
                self.org_id,
                self.id,
                confirmed=True,
            )
        except Exception as exc:
            logger.error(
                "Failed to confirm batch %s for organization %s: %s",
                self.id,
                self.org_id,
                exc,
            )
            raise

        self.confirmed = True
        status = response.get("status", {})
        self.completed = status.get("completed", False)
        self.failed = status.get("failed", False)
        self.errors = status.get("errors", [])

        logger.info("Batch %s confirmed", self.id)
        return self

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self, dashboard=None) -> dict:
        """Fetch and return the current status of this batch from the Meraki API.

        Also updates self.completed, self.failed, and self.errors.

        Args:
            dashboard: Optional DashboardAPI instance. If not provided,
                       get_dashboard() from merakisync is called.

        Returns:
            The status dict from the Meraki API response:
                {
                    "completed": bool,
                    "failed":    bool,
                    "errors":    list[str],
                }

        Raises:
            RuntimeError: If the batch has not been submitted yet (id is None).
        """
        if self.id is None:
            raise RuntimeError(
                "This batch has not been submitted yet. Call create() first."
            )

        if dashboard is None:
            from merakisync.dashboard import get_dashboard
            dashboard = get_dashboard()

        try:
            response = dashboard.organizations.getOrganizationActionBatch(
                self.org_id,
                self.id,
            )
        except Exception as exc:
            logger.error(
                "Failed to fetch status for batch %s: %s",
                self.id,
                exc,
            )
            raise

        status_data = response.get("status", {})
        self.completed = status_data.get("completed", False)
        self.failed = status_data.get("failed", False)
        self.errors = status_data.get("errors", [])

        logger.debug(
            "Batch %s status: completed=%s, failed=%s, errors=%s",
            self.id,
            self.completed,
            self.failed,
            self.errors,
        )
        return status_data

    # ------------------------------------------------------------------
    # Waiting
    # ------------------------------------------------------------------

    def wait_until_complete(
        self,
        *,
        timeout_seconds: float = 120.0,
        poll_interval: float = 3.0,
        dashboard=None,
    ) -> bool:
        """Poll until this batch has completed or failed.

        Not needed for synchronous batches — create() already blocks until
        all actions have finished. For asynchronous batches (the default),
        call this before verify() to avoid comparing against state that
        Meraki has not yet changed.

        Args:
            timeout_seconds: Maximum seconds to wait before raising TimeoutError.
                             Default 120.
            poll_interval:   Seconds between each status poll. Default 3.0.
            dashboard:       Optional DashboardAPI instance. If not provided,
                             get_dashboard() from merakisync is called.

        Returns:
            True if the batch finished with no action-level errors.
            False if any action in the batch failed (partial or total failure).
            Check batch.errors for the specific failures. Changes from
            successful actions in the same batch may still have been applied —
            use verify() to confirm the actual state of each resource.

        Raises:
            RuntimeError: If the batch has not been submitted yet (id is None).
            TimeoutError: If the batch does not finish within timeout_seconds.

        Example:
            batch.create()
            batch.confirm()
            batch.wait_until_complete()
            result = batch.verify()
        """
        if self.id is None:
            raise RuntimeError(
                "This batch has not been submitted yet. Call create() first."
            )

        if self.synchronous:
            # Synchronous batches complete before create() returns.
            logger.debug("Batch %s is synchronous — already complete", self.id)
            return not bool(self.errors)

        if self.completed or self.failed:
            return not bool(self.errors)

        logger.info(
            "Waiting for batch %s to complete (timeout=%.0fs, poll=%.1fs)",
            self.id,
            timeout_seconds,
            poll_interval,
        )

        deadline = time.time() + timeout_seconds

        while not self.completed and not self.failed:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Batch {self.id} did not complete within {timeout_seconds}s. "
                    "Call batch.status() to check the current state."
                )
            time.sleep(min(poll_interval, remaining))
            self.status(dashboard=dashboard)

        if self.errors:
            logger.warning(
                "Batch %s completed with %d error(s): %s",
                self.id,
                len(self.errors),
                self.errors,
            )
        else:
            logger.info("Batch %s completed successfully", self.id)

        return not bool(self.errors)

    @classmethod
    def wait_for_all(
        cls,
        batches: list[ActionBatch],
        *,
        timeout_seconds: float = 120.0,
        poll_interval: float = 3.0,
        dashboard=None,
    ) -> dict[ActionBatch, bool]:
        """Wait for multiple batches to complete, polling all of them together.

        Polls all pending batches each interval and removes them from the wait
        list as they finish. Because Meraki processes async batches in parallel
        on its side, a batch submitted earlier may already be complete by the
        time we first poll it — this method takes advantage of that by checking
        all pending batches each round rather than waiting for each one in turn.

        Synchronous batches are skipped (they complete before create() returns).
        Failed batches do not cause this method to stop — all batches are waited
        on regardless. Check the return value to see which succeeded and which failed.

        Prefer this over calling wait_until_complete() individually when working
        with multiple batches, and call verify_many() after it completes.

        Args:
            batches:         List of ActionBatch objects to wait for. All must
                             have been submitted (id must not be None).
            timeout_seconds: Maximum total seconds to wait before raising
                             TimeoutError. Default 120.
            poll_interval:   Seconds between each round of status polls.
                             Default 3.0.
            dashboard:       Optional DashboardAPI instance.

        Returns:
            A dict mapping each batch to True (finished with no errors) or
            False (one or more actions failed). Meraki's failed=True means at
            least one action failed — not that all actions failed. Changes from
            successful actions in the same batch may still have been applied.
            Check batch.errors for specifics and use verify_many() to confirm
            the actual state of each resource.

        Raises:
            ValueError:   If batches is empty.
            RuntimeError: If any batch has not been submitted yet.
            TimeoutError: If not all batches complete within timeout_seconds.

        Example:
            for batch in batches:
                batch.create()
                batch.confirm()

            ActionBatch.wait_for_all(batches)
            results = ActionBatch.verify_many(batches)
        """
        if not batches:
            raise ValueError("batches cannot be empty")

        unsubmitted = [b for b in batches if b.id is None]
        if unsubmitted:
            raise RuntimeError(
                f"{len(unsubmitted)} batch(es) have not been submitted yet. "
                "Call create() on each batch before calling wait_for_all()."
            )

        # Synchronous batches are already done; only poll async ones.
        pending = [
            b for b in batches
            if not b.synchronous and not b.completed and not b.failed
        ]

        if pending:
            logger.info(
                "Waiting for %d async batch(es) to complete (timeout=%.0fs)",
                len(pending),
                timeout_seconds,
            )

        deadline = time.time() + timeout_seconds

        while pending:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"{len(pending)} batch(es) did not complete within "
                    f"{timeout_seconds}s. "
                    f"Incomplete batch IDs: {[b.id for b in pending]}"
                )

            time.sleep(min(poll_interval, remaining))

            still_pending = []
            for batch in pending:
                batch.status(dashboard=dashboard)
                if batch.completed or batch.failed:
                    if batch.errors:
                        logger.warning(
                            "Batch %s completed with %d error(s): %s",
                            batch.id,
                            len(batch.errors),
                            batch.errors,
                        )
                    else:
                        logger.info("Batch %s completed successfully", batch.id)
                else:
                    still_pending.append(batch)

            pending = still_pending

        logger.info("All %d batch(es) finished", len(batches))
        return {batch: not bool(batch.errors) for batch in batches}

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self) -> VerifyResult:
        """Compare each action's intended state against the current live state.

        Uses bulk fetching to minimize API calls:
          - Device and Network actions: 1 API call per organization
          - Switchport actions: 1 API call per unique switch serial
          - Vlan, Ssid, L3FirewallRule, DhcpServerPolicy: 1 API call per unique network

        When verifying 10 or more actions in total, prefer ActionBatch.verify_many()
        to pool resource fetches across all batches and reduce API calls further.

        Returns:
            A VerifyResult with .verified, .mismatched, .unverifiable, and
            .batch_errors attributes.

        Raises:
            RuntimeError: If the batch has not been submitted yet (id is None).
        """
        if self.id is None:
            raise RuntimeError(
                "This batch has not been submitted yet. Call create() first."
            )

        results = _verify_actions([self], self.org_id)
        return results[self]

    @classmethod
    def verify_many(cls, batches: list[ActionBatch]) -> dict[ActionBatch, VerifyResult]:
        """Verify multiple batches with minimal API calls.

        Preferred over calling batch.verify() individually when sending 10 or
        more actions in total. verify_many() pools all actions across batches
        before fetching, so each resource category is fetched only once regardless
        of how many batches reference it.

        For example, 2000 switchport actions across 20 batches requires 1 API call
        per unique switch serial — typically 20 calls instead of 2000.

        Batches from different organizations are handled correctly: each
        organization's resources are fetched and verified independently.

        Args:
            batches: List of ActionBatch objects to verify. All batches must
                     have been submitted (id must not be None).

        Returns:
            A dict mapping each ActionBatch to a VerifyResult. Each VerifyResult
            has .verified, .mismatched, .unverifiable, and .batch_errors.

        Raises:
            ValueError: If batches is empty.
            RuntimeError: If any batch has not been submitted yet.

        Example:
            results = ActionBatch.verify_many(batches)
            for batch, result in results.items():
                print(f"Batch {batch.id}: {result}")
                for mismatch in result.mismatched:
                    print(f"  {mismatch.action.resource}: {mismatch.mismatches}")
        """
        if not batches:
            raise ValueError("batches cannot be empty")

        unsubmitted = [b for b in batches if b.id is None]
        if unsubmitted:
            raise RuntimeError(
                f"{len(unsubmitted)} batch(es) have not been submitted yet. "
                "Call create() on each batch before calling verify_many()."
            )

        # Group by organization so each org's resources are fetched together.
        by_org: dict[str, list[ActionBatch]] = {}
        for batch in batches:
            by_org.setdefault(batch.org_id, []).append(batch)

        combined: dict[ActionBatch, VerifyResult] = {}
        for org_id, org_batches in by_org.items():
            org_results = _verify_actions(org_batches, org_id)
            combined.update(org_results)

        return combined

    @classmethod
    def run(
        cls,
        actions: list[Action],
        org_id: str,
        *,
        confirmed: bool = False,
        synchronous: bool = False,
        callback: dict | None = None,
        sleep_seconds: float = 5.0,
        timeout_seconds: float = 120.0,
        poll_interval: float = 3.0,
        dashboard=None,
    ) -> VerifyResult:
        """Create batches, submit, wait, and verify in one call.

        Covers the full lifecycle for the common case. Splits actions into
        batches automatically, submits and confirms all of them, waits for
        completion, verifies the results, and returns a single combined
        VerifyResult.

        For retry logic, call run() in a loop using result.mismatched:

            remaining = initial_actions
            for attempt in range(max_retries):
                result = ActionBatch.run(remaining, org_id=org.id)
                remaining = [m.action for m in result.mismatched]
                if not remaining:
                    break

        Args:
            actions:         List of Action objects to submit. Must not be empty.
            org_id: Meraki organization ID that owns these resources.
            confirmed:       Whether batches execute immediately on create().
                             Default False — confirm() is called automatically
                             after create() regardless of this setting.
            synchronous:     Whether Meraki executes the batch synchronously.
                             Default False.
            callback:        Optional Meraki webhook callback config dict.
            sleep_seconds:   Seconds to sleep between batch submissions.
                             Default 5.0.
            timeout_seconds: Maximum seconds to wait for all batches to finish.
                             Default 120.
            poll_interval:   Seconds between status polls. Default 3.0.
            dashboard:       Optional DashboardAPI instance. If not provided,
                             get_dashboard() from merakisync is called.

        Returns:
            A single VerifyResult combining results across all batches, with
            .verified, .mismatched, .unverifiable, and .batch_errors.

        Raises:
            ValueError:   If actions is empty.
            TimeoutError: If batches do not complete within timeout_seconds.
        """
        batches = cls.from_actions(
            actions,
            org_id,
            confirmed=confirmed,
            synchronous=synchronous,
            callback=callback,
        )

        for batch in batches:
            batch.create(dashboard=dashboard, sleep_seconds=sleep_seconds)
            batch.confirm(dashboard=dashboard)

        cls.wait_for_all(
            batches,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
            dashboard=dashboard,
        )

        per_batch = cls.verify_many(batches)

        # Combine all per-batch VerifyResults into one.
        verified: list[Action] = []
        mismatched: list[Mismatch] = []
        unverifiable: list[Action] = []
        batch_errors: list[str] = []

        for result in per_batch.values():
            verified.extend(result.verified)
            mismatched.extend(result.mismatched)
            unverifiable.extend(result.unverifiable)
            batch_errors.extend(result.batch_errors)

        return VerifyResult(
            verified=verified,
            mismatched=mismatched,
            unverifiable=unverifiable,
            batch_errors=batch_errors,
        )


# ------------------------------------------------------------------
# Verify helpers
# ------------------------------------------------------------------

def _pk_key(obj: MerakiObj) -> tuple:
    """Return the primary key values of a MerakiObj as a tuple.

    Uses the model's __pk__ class variable so the key is always consistent
    with how the model identifies itself.

    Example:
        Switchport(serial="Q2AB", port_id="3") → ("Q2AB", "3")
        Vlan(network_id="N_1", vlan_id=100)    → ("N_1", 100)
    """
    return tuple(getattr(obj, field) for field in type(obj).__pk__)


def _bulk_fetch_model(
    model_cls: type,
    source_objs: list[MerakiObj],
    org_id: str,
) -> tuple[dict[tuple, MerakiObj], set[tuple]]:
    """Fetch current state for all objects of one model type in as few API calls as possible.

    Fetch granularity by model:
      - Organization, Network, Device: one call for the entire organization
      - Switchport:                    one call per unique serial
      - Vlan, Ssid, L3FirewallRule,
        DhcpServerPolicy:              one call per unique network_id

    If a fetch group fails (e.g., one switch is unreachable), all source objects
    in that group are added to failed_pk_keys so the caller can mark them
    "unverifiable" rather than misidentifying them as mismatched.

    Returns:
        lookup:          PK tuple → current MerakiObj for successfully fetched resources.
        failed_pk_keys:  PK tuples of source objects whose fetch group encountered an error.
    """
    from merakisync.models.device import Device
    from merakisync.models.dhcp_server_policy import DhcpServerPolicy
    from merakisync.models.l3_firewall_rule import L3FirewallRule
    from merakisync.models.network import Network
    from merakisync.models.organization import Organization
    from merakisync.models.ssid import Ssid
    from merakisync.models.switchport import Switchport
    from merakisync.models.vlan import Vlan

    lookup: dict[tuple, MerakiObj] = {}
    failed_pk_keys: set[tuple] = set()

    # ---- Organization, Network, Device — one call for the whole org ----

    if model_cls is Organization:
        try:
            for obj in Organization.get(source="meraki"):
                lookup[_pk_key(obj)] = obj
        except Exception as exc:
            logger.error("Failed to fetch organizations: %s", exc)
            failed_pk_keys = {_pk_key(obj) for obj in source_objs}

    elif model_cls is Network:
        try:
            for obj in Network.get(org_id, source="meraki"):
                lookup[_pk_key(obj)] = obj
        except Exception as exc:
            logger.error(
                "Failed to fetch networks for organization %s: %s",
                org_id, exc,
            )
            failed_pk_keys = {_pk_key(obj) for obj in source_objs}

    elif model_cls is Device:
        try:
            for obj in Device.get(org_id, source="meraki"):
                lookup[_pk_key(obj)] = obj
        except Exception as exc:
            logger.error(
                "Failed to fetch devices for organization %s: %s",
                org_id, exc,
            )
            failed_pk_keys = {_pk_key(obj) for obj in source_objs}

    # ---- Switchport — one call per unique serial ----

    elif model_cls is Switchport:
        by_serial: dict[str, list[MerakiObj]] = {}
        for obj in source_objs:
            by_serial.setdefault(obj.serial, []).append(obj)

        logger.debug(
            "Fetching switchports: %d unique serial(s)", len(by_serial)
        )
        for serial, group in by_serial.items():
            try:
                for port in Switchport.get(serial, source="meraki"):
                    lookup[_pk_key(port)] = port
            except Exception as exc:
                logger.error(
                    "Failed to fetch switchports for serial %s: %s", serial, exc
                )
                failed_pk_keys.update(_pk_key(obj) for obj in group)

    # ---- Vlan, Ssid, L3FirewallRule, DhcpServerPolicy — one call per network ----

    elif model_cls is Vlan:
        by_network: dict[str, list[MerakiObj]] = {}
        for obj in source_objs:
            by_network.setdefault(obj.network_id, []).append(obj)

        logger.debug("Fetching VLANs: %d unique network(s)", len(by_network))
        for network_id, group in by_network.items():
            try:
                for vlan in Vlan.get(network_id, source="meraki"):
                    lookup[_pk_key(vlan)] = vlan
            except Exception as exc:
                logger.error(
                    "Failed to fetch VLANs for network %s: %s", network_id, exc
                )
                failed_pk_keys.update(_pk_key(obj) for obj in group)

    elif model_cls is Ssid:
        by_network = {}
        for obj in source_objs:
            by_network.setdefault(obj.network_id, []).append(obj)

        logger.debug("Fetching SSIDs: %d unique network(s)", len(by_network))
        for network_id, group in by_network.items():
            try:
                for ssid in Ssid.get(network_id, source="meraki"):
                    lookup[_pk_key(ssid)] = ssid
            except Exception as exc:
                logger.error(
                    "Failed to fetch SSIDs for network %s: %s", network_id, exc
                )
                failed_pk_keys.update(_pk_key(obj) for obj in group)

    elif model_cls is L3FirewallRule:
        by_network = {}
        for obj in source_objs:
            by_network.setdefault(obj.network_id, []).append(obj)

        logger.debug(
            "Fetching L3 firewall rules: %d unique network(s)", len(by_network)
        )
        for network_id, group in by_network.items():
            try:
                for rule in L3FirewallRule.get(network_id, source="meraki"):
                    lookup[_pk_key(rule)] = rule
            except Exception as exc:
                logger.error(
                    "Failed to fetch L3 firewall rules for network %s: %s",
                    network_id, exc,
                )
                failed_pk_keys.update(_pk_key(obj) for obj in group)

    elif model_cls is DhcpServerPolicy:
        by_network = {}
        for obj in source_objs:
            by_network.setdefault(obj.network_id, []).append(obj)

        logger.debug(
            "Fetching DHCP server policies: %d unique network(s)", len(by_network)
        )
        for network_id, group in by_network.items():
            try:
                policy = DhcpServerPolicy.get(network_id, source="meraki")
                if policy is not None:
                    lookup[_pk_key(policy)] = policy
            except Exception as exc:
                logger.error(
                    "Failed to fetch DHCP server policy for network %s: %s",
                    network_id, exc,
                )
                failed_pk_keys.update(_pk_key(obj) for obj in group)

    return lookup, failed_pk_keys


def _verify_actions(
    batches: list[ActionBatch],
    org_id: str,
) -> dict[ActionBatch, VerifyResult]:
    """Core verification logic with bulk fetching.

    Builds one lookup per model type across all batches, then compares each
    action against the in-memory lookup. API calls are proportional to unique
    fetch groups (serials, network IDs, or org-level), not action count.

    Args:
        batches:         Batches to verify. All must belong to org_id.
        org_id: The organization ID shared by all batches.

    Returns:
        A dict mapping each batch to its VerifyResult.
    """
    # Collect all (batch, action) pairs.
    all_pairs: list[tuple[ActionBatch, Action]] = [
        (batch, action)
        for batch in batches
        for action in batch.actions
    ]

    total_actions = len(all_pairs)
    logger.info(
        "Verifying %d action(s) across %d batch(es) for organization %s",
        total_actions,
        len(batches),
        org_id,
    )

    # Group source objects by model type across all batches.
    objs_by_model: dict[type, list[MerakiObj]] = {}
    for _, action in all_pairs:
        if action.source_obj is not None:
            cls = type(action.source_obj)
            objs_by_model.setdefault(cls, []).append(action.source_obj)

    # Bulk fetch once per model type.
    lookup: dict[type, dict[tuple, MerakiObj]] = {}
    failed_keys: dict[type, set[tuple]] = {}
    for cls, source_objs in objs_by_model.items():
        model_lookup, model_failed = _bulk_fetch_model(cls, source_objs, org_id)
        lookup[cls] = model_lookup
        failed_keys[cls] = model_failed

    # Build per-batch lists, then construct VerifyResult objects at the end.
    verified_by_batch: dict[ActionBatch, list[Action]] = {b: [] for b in batches}
    mismatched_by_batch: dict[ActionBatch, list[Mismatch]] = {b: [] for b in batches}
    unverifiable_by_batch: dict[ActionBatch, list[Action]] = {b: [] for b in batches}

    # Compare each action against the lookup.
    for batch, action in all_pairs:
        obj = action.source_obj

        if obj is None:
            unverifiable_by_batch[batch].append(action)
            continue

        cls = type(obj)
        pk = _pk_key(obj)

        if cls not in lookup:
            # Model type is not supported by the verify registry.
            unverifiable_by_batch[batch].append(action)
            continue

        if pk in failed_keys.get(cls, set()):
            # The API call for this resource's fetch group failed.
            unverifiable_by_batch[batch].append(action)
            continue

        current = lookup[cls].get(pk)

        if action.operation == "destroy":
            # For destroys: resource not found means success.
            # current is None here only because the fetch succeeded but the
            # resource was not returned — confirming it no longer exists.
            if current is None:
                verified_by_batch[batch].append(action)
            else:
                logger.warning(
                    "Destroy verification failed for %r — resource still exists",
                    action.resource,
                )
                mismatched_by_batch[batch].append(Mismatch(
                    action=action,
                    mismatches={
                        "existence": {
                            "expected": "destroyed",
                            "actual": "still exists",
                        }
                    },
                ))
            continue

        if current is None:
            unverifiable_by_batch[batch].append(action)
            continue

        if not action.body:
            verified_by_batch[batch].append(action)
            continue

        current_meraki = current.to_meraki_dict()
        mismatches = {}
        for api_key, expected in action.body.items():
            actual = current_meraki.get(api_key)
            if actual != expected:
                mismatches[api_key] = {"expected": expected, "actual": actual}

        if mismatches:
            logger.warning(
                "Verification failed for %r: %s", action.resource, mismatches
            )
            mismatched_by_batch[batch].append(Mismatch(action=action, mismatches=mismatches))
        else:
            verified_by_batch[batch].append(action)

    results = {
        batch: VerifyResult(
            verified=verified_by_batch[batch],
            mismatched=mismatched_by_batch[batch],
            unverifiable=unverifiable_by_batch[batch],
            batch_errors=batch.errors,
        )
        for batch in batches
    }

    total_verified = sum(len(r.verified) for r in results.values())
    total_mismatched = sum(len(r.mismatched) for r in results.values())
    total_unverifiable = sum(len(r.unverifiable) for r in results.values())
    logger.info(
        "Verify complete: %d verified, %d mismatched, %d unverifiable",
        total_verified,
        total_mismatched,
        total_unverifiable,
    )
    return results
