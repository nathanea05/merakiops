"""Unit tests for Action — action.py."""

from __future__ import annotations

import pytest

from merakisync.models.dhcp_server_policy import DhcpServerPolicy
from merakisync.models.l3_firewall_rule import L3FirewallRule
from merakisync.models.network import Network
from merakisync.models.switchport import Switchport
from merakisync.models.vlan import Vlan

from merakiops.action import Action


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_port(vlan: int = 100) -> Switchport:
    return Switchport(serial="Q2AB-1234-5678", port_id="3", name="test-port", vlan=vlan)


def make_vlan(network_id: str = "N_1", vlan_id: int = 100) -> Vlan:
    return Vlan(network_id=network_id, vlan_id=vlan_id, name="TestVlan")


def make_network() -> Network:
    return Network(id="N_1", organization_id="123456", name="TestNet")


# ---------------------------------------------------------------------------
# __post_init__ validation
# ---------------------------------------------------------------------------


class TestActionValidation:
    def test_empty_resource_raises(self):
        with pytest.raises(ValueError, match="resource must be a non-empty string"):
            Action(resource="", operation="update", body={})

    def test_invalid_operation_raises(self):
        with pytest.raises(ValueError, match="operation must be one of"):
            Action(resource="/some/path", operation="patch", body={})

    def test_valid_operations_accepted(self):
        for op in ("create", "update", "destroy"):
            a = Action(resource="/some/path", operation=op, body=None)
            assert a.operation == op

    def test_frozen_after_creation(self):
        a = Action(resource="/some/path", operation="update", body={"vlan": 200})
        with pytest.raises((AttributeError, TypeError)):
            a.resource = "/other/path"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Action.update()
# ---------------------------------------------------------------------------


class TestActionUpdate:
    def test_update_uses_changed_fields(self):
        port = make_port()
        port.vlan = 200
        action = Action.update(port)

        assert action.operation == "update"
        assert action.resource == port.resource_path
        assert action.body == {"vlan": 200}
        assert action.source_obj is port

    def test_update_multiple_changed_fields(self):
        port = make_port()
        port.vlan = 200
        port.name = "uplink"
        action = Action.update(port)

        assert "vlan" in action.body
        assert "name" in action.body

    def test_update_no_changes_raises(self):
        port = make_port()
        with pytest.raises(ValueError, match="no changed fields"):
            Action.update(port)

    def test_update_fields_to_update_overrides_change_tracking(self):
        port = make_port()
        # No changes made — but we supply explicit fields
        action = Action.update(port, fields_to_update=["vlan"])
        assert action.body == {"vlan": 100}

    def test_update_fields_to_update_no_match_raises(self):
        port = make_port()
        with pytest.raises(ValueError, match="None of the field names in fields_to_update matched"):
            Action.update(port, fields_to_update=["nonexistent_field"])

    def test_update_fields_to_update_ignores_changed_fields(self):
        port = make_port()
        port.name = "changed-name"
        # Explicitly request only vlan, not name
        action = Action.update(port, fields_to_update=["vlan"])
        assert "name" not in action.body
        assert "vlan" in action.body

    def test_update_resource_path_is_single_resource(self):
        port = make_port()
        port.vlan = 200
        action = Action.update(port)
        # Should include the port_id, not a collection path
        assert port.port_id in action.resource


# ---------------------------------------------------------------------------
# Action.create()
# ---------------------------------------------------------------------------


class TestActionCreate:
    def test_create_uses_collection_path(self):
        vlan = make_vlan()
        action = Action.create(vlan)

        assert action.operation == "create"
        # Collection path = single-resource path minus last segment
        assert action.resource == "/networks/N_1/appliance/vlans"
        assert action.body is not None
        assert action.source_obj is vlan

    def test_create_body_includes_all_fields(self):
        vlan = make_vlan()
        action = Action.create(vlan)
        assert "id" in action.body or "networkId" in action.body

    def test_create_custom_resource_override(self):
        vlan = make_vlan()
        action = Action.create(vlan, resource="/custom/path")
        assert action.resource == "/custom/path"

    def test_create_network_collection_path(self):
        net = make_network()
        action = Action.create(net)
        # /networks/N_1 → /networks
        assert action.resource == "/networks"

    def test_create_switchport_collection_path(self):
        port = make_port()
        action = Action.create(port)
        # /devices/Q2AB-1234-5678/switch/ports/3 → /devices/Q2AB-1234-5678/switch/ports
        assert action.resource == "/devices/Q2AB-1234-5678/switch/ports"


# ---------------------------------------------------------------------------
# Action.destroy()
# ---------------------------------------------------------------------------


class TestActionDestroy:
    def test_destroy_uses_single_resource_path(self):
        vlan = make_vlan()
        action = Action.destroy(vlan)

        assert action.operation == "destroy"
        assert action.resource == vlan.resource_path
        assert action.body is None
        assert action.source_obj is vlan

    def test_destroy_port(self):
        port = make_port()
        action = Action.destroy(port)
        assert action.resource == port.resource_path
        assert action.body is None


# ---------------------------------------------------------------------------
# Action.to_meraki_action()
# ---------------------------------------------------------------------------


class TestToMerakiAction:
    def test_update_includes_body(self):
        port = make_port()
        port.vlan = 200
        action = Action.update(port)
        result = action.to_meraki_action()

        assert result["resource"] == action.resource
        assert result["operation"] == "update"
        assert result["body"] == {"vlan": 200}

    def test_destroy_omits_body_key(self):
        vlan = make_vlan()
        action = Action.destroy(vlan)
        result = action.to_meraki_action()

        assert "body" not in result
        assert result["operation"] == "destroy"
        assert result["resource"] == vlan.resource_path

    def test_create_includes_body(self):
        vlan = make_vlan()
        action = Action.create(vlan)
        result = action.to_meraki_action()

        assert "body" in result
        assert result["operation"] == "create"

    def test_action_with_none_body_omits_body_key(self):
        action = Action(resource="/some/path", operation="destroy", body=None)
        result = action.to_meraki_action()
        assert "body" not in result

    def test_action_with_empty_dict_body_includes_body_key(self):
        # An empty dict body is different from None — it should be included.
        action = Action(resource="/some/path", operation="update", body={})
        result = action.to_meraki_action()
        assert "body" in result


# ---------------------------------------------------------------------------
# source_obj is stored
# ---------------------------------------------------------------------------


class TestSourceObj:
    def test_update_stores_source_obj(self):
        port = make_port()
        port.vlan = 200
        action = Action.update(port)
        assert action.source_obj is port

    def test_create_stores_source_obj(self):
        vlan = make_vlan()
        action = Action.create(vlan)
        assert action.source_obj is vlan

    def test_destroy_stores_source_obj(self):
        vlan = make_vlan()
        action = Action.destroy(vlan)
        assert action.source_obj is vlan

    def test_source_obj_not_included_in_equality(self):
        port1 = make_port()
        port1.vlan = 200
        port2 = make_port()
        port2.vlan = 200
        a1 = Action.update(port1)
        a2 = Action.update(port2)
        # source_obj has compare=False so two identical actions are equal
        assert a1 == a2

    def test_source_obj_not_included_in_repr(self):
        port = make_port()
        port.vlan = 200
        action = Action.update(port)
        assert "source_obj" not in repr(action)


# ---------------------------------------------------------------------------
# L3FirewallRule and DhcpServerPolicy use update() not create()
# ---------------------------------------------------------------------------


class TestSpecialModels:
    def test_l3_firewall_rule_update(self):
        rule = L3FirewallRule(
            network_id="N_1",
            rule_order=1,
            comment="allow-http",
            policy="allow",
            protocol="tcp",
            dest_port="80",
            dest_cidr="any",
            src_port="any",
            src_cidr="any",
            syslog_enabled=False,
        )
        rule.comment = "allow-https"
        action = Action.update(rule)
        assert action.operation == "update"
        # resource_path for L3FirewallRule is already the collection endpoint
        assert "l3FirewallRules" in action.resource

    def test_dhcp_server_policy_update(self):
        policy = DhcpServerPolicy(network_id="N_1", default_policy="block")
        policy.default_policy = "allow"
        action = Action.update(policy)
        assert action.operation == "update"
        assert "dhcpServerPolicy" in action.resource
