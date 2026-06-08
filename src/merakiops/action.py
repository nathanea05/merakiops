from __future__ import annotations

import logging
from dataclasses import dataclass, field

from merakisync.models.base import MerakiObj

logger = logging.getLogger(__name__)

_VALID_OPERATIONS = frozenset({"create", "update", "destroy"})


@dataclass(frozen=True)
class Action:
    """A single Meraki API change for use inside an action batch.

    Always create Actions using the factory classmethods — never instantiate directly:

        action = Action.update(switchport)   # changed fields only
        action = Action.create(vlan)         # all fields, collection endpoint
        action = Action.destroy(vlan)        # no body, single-resource endpoint
        action = Action.raw(path, "update", body)  # no merakisync model

    Each Action corresponds to one API call in the batch. The Meraki API
    supports three operations: create, update, and destroy.

    Meraki API reference:
        https://developer.cisco.com/meraki/api-v1/create-organization-action-batch/
    """

    resource: str
    operation: str
    body: dict | None
    source_obj: MerakiObj | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.resource:
            raise ValueError("resource must be a non-empty string")
        if self.operation not in _VALID_OPERATIONS:
            raise ValueError(
                f"operation must be one of {sorted(_VALID_OPERATIONS)}, "
                f"got {self.operation!r}"
            )

    @classmethod
    def update(
        cls,
        obj: MerakiObj,
        *,
        fields_to_update: list[str] | None = None,
    ) -> Action:
        """Create an update action from a MerakiObj.

        By default, only the fields that have been modified since the object
        was loaded are included in the request body (tracked automatically via
        _changed_fields).

        Use fields_to_update to bypass change tracking and explicitly specify
        which fields to include. This is useful when restoring an object to a
        previous state retrieved from the database — the object already holds
        the desired values but no fields have been mutated in the current
        session, so _changed_fields would be empty.

        Args:
            obj:              A MerakiObj instance.
            fields_to_update: Optional list of snake_case field names to
                              include in the update body. When provided,
                              _changed_fields is ignored entirely.
                              Example: fields_to_update=["stp_guard", "vlan"]

        Raises:
            ValueError: If no fields to update are found — either because
                        no fields were changed (default mode) or because none
                        of the names in fields_to_update matched the model's
                        fields (explicit mode).

        Examples:
            # Default: include only fields changed in this session
            port.vlan = 200
            action = Action.update(port)

            # Explicit: restore specific fields from a historical record
            old_port = Switchport.get(serial="Q2AB", source="database", ts=restore_time)[0]
            action = Action.update(old_port, fields_to_update=["stp_guard", "vlan"])
        """
        fields = fields_to_update if fields_to_update is not None else list(obj._changed_fields)

        body = obj.to_meraki_dict(fields=fields)

        if not body:
            if fields_to_update is not None:
                raise ValueError(
                    f"None of the field names in fields_to_update matched "
                    f"{type(obj).__name__}: {fields_to_update}. "
                    "Use snake_case field names as defined on the model."
                )
            raise ValueError(
                f"{type(obj).__name__} has no changed fields. "
                "Assign at least one new value before calling Action.update(), "
                "or pass fields_to_update to specify fields explicitly."
            )

        return cls(
            resource=obj.resource_path,
            operation="update",
            body=body,
            source_obj=obj,
        )

    @classmethod
    def create(cls, obj: MerakiObj, *, resource: str | None = None) -> Action:
        """Create a create action from a MerakiObj.

        All non-versioning fields are included in the request body. The
        resource path defaults to the collection endpoint, derived by
        stripping the last segment from obj.resource_path.

        For example:
            Vlan.resource_path  = "/networks/{id}/appliance/vlans/{vlan_id}"
            create() resource   = "/networks/{id}/appliance/vlans"

        Override resource if the auto-derived path is incorrect for the model.
        L3FirewallRule and DhcpServerPolicy do not support create operations —
        use Action.update() for those models instead.

        Args:
            obj:      A MerakiObj instance with all required fields set.
            resource: Optional override for the collection endpoint path.

        Example:
            new_vlan = Vlan(network_id="N_123", vlan_id=200, name="Finance", ...)
            action = Action.create(new_vlan)
        """
        body = obj.to_meraki_dict()
        if resource is None:
            # Derive the collection path by removing the last path segment.
            # This works for most models (Vlan, Device, Network, Ssid, Switchport).
            # For models where resource_path is already the collection endpoint
            # (L3FirewallRule, DhcpServerPolicy), use Action.update() instead.
            resource = "/".join(obj.resource_path.rstrip("/").split("/")[:-1])
        return cls(
            resource=resource,
            operation="create",
            body=body,
            source_obj=obj,
        )

    @classmethod
    def raw(
        cls,
        resource: str,
        operation: str,
        body: dict | None = None,
    ) -> Action:
        """Create an Action for an API endpoint with no merakisync model.

        Use this for endpoints that do not have a corresponding MerakiObj in
        merakisync. Because there is no model to compare against, these actions
        will always appear in VerifyResult.unverifiable — this is expected, not
        an error. Check result.batch_errors to confirm that execution succeeded.

        Args:
            resource:  The Meraki API resource path.
                       Example: "/networks/N_abc/appliance/security/malware"
            operation: One of "create", "update", or "destroy".
            body:      The request body dict, or None for destroy operations.

        Raises:
            ValueError: If resource is empty or operation is not valid.

        Example:
            actions = [
                Action.raw(
                    resource=f"/networks/{net_id}/appliance/security/malware",
                    operation="update",
                    body={"mode": "enabled"},
                )
                for net_id in network_ids
            ]
            result = ActionBatch.run("123456", actions, confirmed=True)
            if result.batch_errors:
                print("Errors:", result.batch_errors)
            else:
                print(f"{len(result.unverifiable)} actions completed")
        """
        return cls(
            resource=resource,
            operation=operation,
            body=body,
            source_obj=None,
        )

    @classmethod
    def destroy(cls, obj: MerakiObj) -> Action:
        """Create a destroy action from a MerakiObj.

        No body is sent — the resource is identified by its path alone.
        The resource path comes from obj.resource_path.

        Args:
            obj: A MerakiObj instance identifying the resource to delete.

        Example:
            action = Action.destroy(old_vlan)
        """
        return cls(
            resource=obj.resource_path,
            operation="destroy",
            body=None,
            source_obj=obj,
        )

    def to_meraki_action(self) -> dict:
        """Return this action as a dict formatted for the Meraki API.

        The body key is omitted entirely for destroy operations, as
        required by the Meraki API.

        Returns:
            A dict with keys: resource, operation, and optionally body.
        """
        action: dict = {
            "resource": self.resource,
            "operation": self.operation,
        }
        if self.body is not None:
            action["body"] = self.body
        return action
