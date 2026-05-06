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
    def update(cls, obj: MerakiObj) -> Action:
        """Create an update action from a MerakiObj with changed fields.

        Only the fields that have been modified since the object was loaded
        are included in the request body. The resource path comes from
        obj.resource_path.

        Args:
            obj: A MerakiObj instance with one or more modified fields.

        Raises:
            ValueError: If no fields have been changed on the object.

        Example:
            port.vlan = 200
            action = Action.update(port)
        """
        body = obj.to_meraki_dict(fields=list(obj._changed_fields))
        if not body:
            raise ValueError(
                f"{type(obj).__name__} has no changed fields. "
                "Assign at least one new value before calling Action.update()."
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
