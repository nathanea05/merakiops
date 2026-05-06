from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from merakiops.action import Action

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
            organization_id="123456",
            confirmed=False,
        )
        for batch in batches:
            batch.create()
            batch.confirm()
            batch.status()
            result = batch.verify()
    """

    def __init__(
        self,
        organization_id: str,
        actions: list[Action],
        *,
        confirmed: bool = False,
        synchronous: bool = False,
        callback: dict | None = None,
    ) -> None:
        self.organization_id = organization_id
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
            f"organization_id={self.organization_id!r}, "
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
        actions: list[Action],
        organization_id: str,
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
            organization_id: Meraki organization ID that owns these resources.
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
                organization_id="123456",
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
                    organization_id=organization_id,
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
            organization_id,
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
            self.organization_id,
            self.confirmed,
            self.synchronous,
        )

        try:
            response = dashboard.organizations.createOrganizationActionBatch(
                self.organization_id,
                **payload,
            )
        except Exception as exc:
            logger.error(
                "Failed to submit action batch to organization %s: %s",
                self.organization_id,
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
                self.organization_id,
                self.id,
                confirmed=True,
            )
        except Exception as exc:
            logger.error(
                "Failed to confirm batch %s for organization %s: %s",
                self.id,
                self.organization_id,
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
                self.organization_id,
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
    # Verification
    # ------------------------------------------------------------------

    def verify(self) -> dict:
        """Compare each action's intended state against the current live state.

        For each action in this batch, fetches the resource's current state
        from the Meraki API using merakisync's model.get(source="meraki") and
        compares it against the fields in action.body.

        Note: Unlike create(), confirm(), and status(), this method does not
        accept a dashboard argument. merakisync's model.get() manages its own
        API connectivity via get_dashboard() internally.

        Supported models: Network, Device, Switchport, Vlan, Ssid,
        L3FirewallRule, DhcpServerPolicy, Organization.

        Returns:
            A dict with three keys:
                "verified":      list of Actions where all body fields matched.
                "mismatched":    list of dicts, each with keys "action" and
                                 "mismatches". mismatches maps camelCase field
                                 names to {"expected": ..., "actual": ...}.
                "unverifiable":  list of Actions that could not be checked,
                                 either because source_obj was not stored or
                                 because the model type is not in the registry.
        """
        if self.id is None:
            raise RuntimeError(
                "This batch has not been submitted yet. Call create() first."
            )

        results: dict = {
            "verified": [],
            "mismatched": [],
            "unverifiable": [],
        }

        logger.info("Verifying %d action(s) in batch %s", len(self.actions), self.id)

        for action in self.actions:
            if action.source_obj is None:
                logger.debug(
                    "Action on resource %r has no source_obj — skipping verification",
                    action.resource,
                )
                results["unverifiable"].append(action)
                continue

            is_supported, current = _fetch_current_state(
                action, self.organization_id
            )

            if not is_supported:
                logger.debug(
                    "Model type %r is not in the verify registry — skipping",
                    type(action.source_obj).__name__,
                )
                results["unverifiable"].append(action)
                continue

            if action.operation == "destroy":
                if current is None:
                    results["verified"].append(action)
                else:
                    logger.warning(
                        "Destroy verification failed for %r — resource still exists",
                        action.resource,
                    )
                    results["mismatched"].append({
                        "action": action,
                        "mismatches": {
                            "existence": {
                                "expected": "destroyed",
                                "actual": "still exists",
                            }
                        },
                    })
                continue

            if current is None:
                logger.debug(
                    "Could not fetch current state for %r — marking unverifiable",
                    action.resource,
                )
                results["unverifiable"].append(action)
                continue

            if not action.body:
                results["verified"].append(action)
                continue

            current_meraki = current.to_meraki_dict()
            mismatches = {}
            for api_key, expected in action.body.items():
                actual = current_meraki.get(api_key)
                if actual != expected:
                    mismatches[api_key] = {"expected": expected, "actual": actual}

            if mismatches:
                logger.warning(
                    "Verification failed for %r: %s",
                    action.resource,
                    mismatches,
                )
                results["mismatched"].append({"action": action, "mismatches": mismatches})
            else:
                results["verified"].append(action)

        logger.info(
            "Batch %s verify complete: %d verified, %d mismatched, %d unverifiable",
            self.id,
            len(results["verified"]),
            len(results["mismatched"]),
            len(results["unverifiable"]),
        )
        return results


# ------------------------------------------------------------------
# Verify helpers
# ------------------------------------------------------------------

def _fetch_current_state(
    action: Action,
    organization_id: str,
) -> tuple[bool, MerakiObj | None]:
    """Fetch the current live state of the resource referenced by an action.

    Imports are deferred to avoid circular imports from merakisync.

    Returns:
        (is_supported, current_obj) where:
            is_supported=False  — model type is not in the verify registry
            is_supported=True, obj=None  — model supported; resource not found
            is_supported=True, obj=...   — model supported; current state retrieved
    """
    from merakisync.models.device import Device
    from merakisync.models.dhcp_server_policy import DhcpServerPolicy
    from merakisync.models.l3_firewall_rule import L3FirewallRule
    from merakisync.models.network import Network
    from merakisync.models.organization import Organization
    from merakisync.models.ssid import Ssid
    from merakisync.models.switchport import Switchport
    from merakisync.models.vlan import Vlan

    obj = action.source_obj
    cls = type(obj)

    if cls is Network:
        results = Network.get(organization_id, source="meraki", network_id=obj.id)
        return True, (results[0] if results else None)

    elif cls is Device:
        results = Device.get(organization_id, source="meraki", serial=obj.serial)
        return True, (results[0] if results else None)

    elif cls is Switchport:
        results = Switchport.get(obj.serial, source="meraki", port_id=obj.port_id)
        return True, (results[0] if results else None)

    elif cls is Vlan:
        results = Vlan.get(obj.network_id, source="meraki", vlan_id=obj.vlan_id)
        return True, (results[0] if results else None)

    elif cls is Ssid:
        results = Ssid.get(obj.network_id, source="meraki", number=obj.number)
        return True, (results[0] if results else None)

    elif cls is L3FirewallRule:
        # No per-rule API endpoint — fetch all rules for the network and match by position.
        results = L3FirewallRule.get(obj.network_id, source="meraki")
        for rule in results:
            if rule.rule_order == obj.rule_order:
                return True, rule
        return True, None

    elif cls is DhcpServerPolicy:
        # One policy per network — get() returns a single instance or None.
        current = DhcpServerPolicy.get(obj.network_id, source="meraki")
        return True, current

    elif cls is Organization:
        # get() returns all organizations — filter by id.
        results = Organization.get(source="meraki")
        for org in results:
            if org.id == obj.id:
                return True, org
        return True, None

    return False, None
