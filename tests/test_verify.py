"""Unit tests for verification logic — _bulk_fetch_model and _verify_actions.

All merakisync .get() calls are mocked so these tests are fully offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from merakisync.models.device import Device
from merakisync.models.dhcp_server_policy import DhcpServerPolicy
from merakisync.models.l3_firewall_rule import L3FirewallRule
from merakisync.models.network import Network
from merakisync.models.organization import Organization
from merakisync.models.ssid import Ssid
from merakisync.models.switchport import Switchport
from merakisync.models.vlan import Vlan

from merakiops.action import Action
from merakiops.action_batch import ActionBatch, _bulk_fetch_model, _pk_key, _verify_actions
from merakiops.result import Mismatch, VerifyResult


# ---------------------------------------------------------------------------
# Helpers — build model objects and actions
# ---------------------------------------------------------------------------

ORG = "123456"


def port(serial: str = "Q2AB", port_id: str = "1", vlan: int = 100) -> Switchport:
    return Switchport(serial=serial, port_id=port_id, name="p", vlan=vlan)


def vlan_obj(network_id: str = "N_1", vlan_id: int = 100, name: str = "v") -> Vlan:
    return Vlan(network_id=network_id, vlan_id=vlan_id, name=name)


def net(nid: str = "N_1", name: str = "net") -> Network:
    return Network(id=nid, organization_id=ORG, name=name)


def dev(serial: str = "Q2AB", name: str = "sw1") -> Device:
    return Device(serial=serial, name=name)


def org_obj(org_id: str = ORG) -> Organization:
    return Organization(id=org_id, name="TestOrg", url="http://x")


def ssid_obj(network_id: str = "N_1", number: int = 0, name: str = "ssid") -> Ssid:
    return Ssid(network_id=network_id, number=number, name=name)


def rule(network_id: str = "N_1", rule_order: int = 1, comment: str = "r") -> L3FirewallRule:
    return L3FirewallRule(
        network_id=network_id, rule_order=rule_order, comment=comment,
        policy="allow", protocol="tcp", dest_port="80", dest_cidr="any",
        src_port="any", src_cidr="any", syslog_enabled=False,
    )


def dhcp(network_id: str = "N_1", default_policy: str = "block") -> DhcpServerPolicy:
    return DhcpServerPolicy(network_id=network_id, default_policy=default_policy)


def submitted_batch(actions: list) -> ActionBatch:
    b = ActionBatch(org_id=ORG, actions=actions)
    b.id = "batch_001"
    return b



# ---------------------------------------------------------------------------
# _pk_key
# ---------------------------------------------------------------------------


class TestPkKey:
    def test_switchport_pk(self):
        p = port(serial="Q2AB", port_id="3")
        assert _pk_key(p) == ("Q2AB", "3")

    def test_vlan_pk(self):
        v = vlan_obj(network_id="N_abc", vlan_id=200)
        assert _pk_key(v) == ("N_abc", 200)

    def test_network_pk(self):
        n = net(nid="N_1")
        assert _pk_key(n) == ("N_1",)

    def test_device_pk(self):
        d = dev(serial="QABC")
        assert _pk_key(d) == ("QABC",)


# ---------------------------------------------------------------------------
# batch.verify() — single batch happy path
# ---------------------------------------------------------------------------


class TestSingleBatchVerify:
    def test_verify_returns_verify_result(self):
        v = vlan_obj()
        v.name = "updated"
        action = Action.update(v)
        batch = submitted_batch([action])

        live_vlan = vlan_obj(name="updated")
        with patch("merakisync.models.vlan.Vlan.get", return_value=[live_vlan]):
            result = batch.verify()

        assert isinstance(result, VerifyResult)
        assert action in result.verified

    def test_verify_returns_result_for_correct_batch(self):
        v = vlan_obj()
        v.name = "new"
        action = Action.update(v)
        batch = submitted_batch([action])

        with patch("merakisync.models.vlan.Vlan.get", return_value=[]):
            result = batch.verify()

        assert action in result.unverifiable


# ---------------------------------------------------------------------------
# verify_many() guards
# ---------------------------------------------------------------------------


class TestVerifyManyGuards:
    def test_empty_batches_raises(self):
        with pytest.raises(ValueError, match="batches cannot be empty"):
            ActionBatch.verify_many([])

    def test_unsubmitted_batch_raises(self):
        b = ActionBatch(org_id=ORG, actions=[Action.destroy(vlan_obj())])
        with pytest.raises(RuntimeError, match="have not been submitted yet"):
            ActionBatch.verify_many([b])


# ---------------------------------------------------------------------------
# verify() guard
# ---------------------------------------------------------------------------


class TestVerifyGuard:
    def test_verify_not_submitted_raises(self):
        b = ActionBatch(org_id=ORG, actions=[Action.destroy(vlan_obj())])
        with pytest.raises(RuntimeError, match="not been submitted yet"):
            b.verify()


# ---------------------------------------------------------------------------
# Actions with no source_obj → unverifiable
# ---------------------------------------------------------------------------


class TestNoSourceObj:
    def test_action_without_source_obj_is_unverifiable(self):
        action = Action(resource="/networks/N_1/appliance/vlans/100", operation="update", body={"name": "x"})
        assert action.source_obj is None

        batch = submitted_batch([action])
        results = _verify_actions([batch], ORG)
        result = results[batch]

        assert action in result.unverifiable
        assert len(result.verified) == 0
        assert len(result.mismatched) == 0


# ---------------------------------------------------------------------------
# Unsupported model type → unverifiable
# ---------------------------------------------------------------------------


class TestUnsupportedModel:
    def test_unsupported_model_is_unverifiable(self):
        from merakisync.models.base import MerakiObj
        import dataclasses

        @dataclasses.dataclass
        class FakeModel(MerakiObj):
            __table_name__ = "fake"
            __pk__ = ("id",)
            id: str = "fake_1"
            label: str = "old"

            @property
            def resource_path(self) -> str:
                return f"/fake/{self.id}"

        obj = FakeModel()
        obj.label = "new"
        # Create action bypassing the factory so source_obj is set to the unsupported type
        action = Action(
            resource=obj.resource_path,
            operation="update",
            body={"label": "new"},
            source_obj=obj,
        )

        batch = submitted_batch([action])
        results = _verify_actions([batch], ORG)
        result = results[batch]

        assert action in result.unverifiable


# ---------------------------------------------------------------------------
# Destroy verification
# ---------------------------------------------------------------------------


class TestDestroyVerification:
    def test_destroy_verified_when_resource_not_found(self):
        v = vlan_obj()
        action = Action.destroy(v)
        batch = submitted_batch([action])

        with patch("merakisync.models.vlan.Vlan.get", return_value=[]):
            results = _verify_actions([batch], ORG)

        result = results[batch]
        assert action in result.verified
        assert len(result.mismatched) == 0

    def test_destroy_mismatched_when_resource_still_exists(self):
        v = vlan_obj()
        action = Action.destroy(v)
        batch = submitted_batch([action])

        live_vlan = vlan_obj()
        with patch("merakisync.models.vlan.Vlan.get", return_value=[live_vlan]):
            results = _verify_actions([batch], ORG)

        result = results[batch]
        assert len(result.mismatched) == 1
        assert result.mismatched[0].action is action
        assert "existence" in result.mismatched[0].mismatches


# ---------------------------------------------------------------------------
# Update verification
# ---------------------------------------------------------------------------


class TestUpdateVerification:
    def test_update_verified_when_all_fields_match(self):
        p = port(vlan=100)
        p.vlan = 200
        action = Action.update(p)
        batch = submitted_batch([action])

        live_port = port(vlan=200)
        with patch("merakisync.models.switchport.Switchport.get", return_value=[live_port]):
            results = _verify_actions([batch], ORG)

        result = results[batch]
        assert action in result.verified
        assert len(result.mismatched) == 0

    def test_update_mismatched_when_field_differs(self):
        p = port(vlan=100)
        p.vlan = 200
        action = Action.update(p)
        batch = submitted_batch([action])

        live_port = port(vlan=100)  # still 100 — change didn't apply
        with patch("merakisync.models.switchport.Switchport.get", return_value=[live_port]):
            results = _verify_actions([batch], ORG)

        result = results[batch]
        assert len(result.mismatched) == 1
        mismatch = result.mismatched[0]
        assert mismatch.action is action
        assert "vlan" in mismatch.mismatches
        assert mismatch.mismatches["vlan"]["expected"] == 200
        assert mismatch.mismatches["vlan"]["actual"] == 100

    def test_update_unverifiable_when_resource_not_found(self):
        p = port(vlan=100)
        p.vlan = 200
        action = Action.update(p)
        batch = submitted_batch([action])

        with patch("merakisync.models.switchport.Switchport.get", return_value=[]):
            results = _verify_actions([batch], ORG)

        result = results[batch]
        assert action in result.unverifiable

    def test_empty_body_action_is_verified(self):
        v = vlan_obj()
        action = Action(
            resource=v.resource_path,
            operation="update",
            body={},
            source_obj=v,
        )
        batch = submitted_batch([action])

        live_vlan = vlan_obj()
        with patch("merakisync.models.vlan.Vlan.get", return_value=[live_vlan]):
            results = _verify_actions([batch], ORG)

        assert action in results[batch].verified


# ---------------------------------------------------------------------------
# Fetch error → unverifiable (not mismatched)
# ---------------------------------------------------------------------------


class TestFetchError:
    def test_fetch_error_marks_group_unverifiable(self):
        p = port(vlan=100)
        p.vlan = 200
        action = Action.update(p)
        batch = submitted_batch([action])

        with patch("merakisync.models.switchport.Switchport.get", side_effect=Exception("unreachable")):
            results = _verify_actions([batch], ORG)

        result = results[batch]
        assert action in result.unverifiable
        assert len(result.mismatched) == 0

    def test_fetch_error_on_one_serial_does_not_affect_another(self):
        p1 = port(serial="SERIAL1", vlan=100)
        p1.vlan = 200
        action1 = Action.update(p1)

        p2 = port(serial="SERIAL2", vlan=100)
        p2.vlan = 200
        action2 = Action.update(p2)

        batch = submitted_batch([action1, action2])

        live_p2 = port(serial="SERIAL2", vlan=200)

        def mock_get(serial, source):
            if serial == "SERIAL1":
                raise Exception("unreachable")
            return [live_p2]

        with patch("merakisync.models.switchport.Switchport.get", side_effect=mock_get):
            results = _verify_actions([batch], ORG)

        result = results[batch]
        assert action1 in result.unverifiable
        assert action2 in result.verified


# ---------------------------------------------------------------------------
# batch_errors from batch.errors
# ---------------------------------------------------------------------------


class TestBatchErrors:
    def test_batch_errors_included_in_result(self):
        v = vlan_obj()
        v.name = "new"
        action = Action.update(v)
        batch = submitted_batch([action])
        batch.errors = ["Action 1 failed: invalid vlan id"]

        live_vlan = vlan_obj(name="new")
        with patch("merakisync.models.vlan.Vlan.get", return_value=[live_vlan]):
            results = _verify_actions([batch], ORG)

        result = results[batch]
        assert result.batch_errors == ["Action 1 failed: invalid vlan id"]


# ---------------------------------------------------------------------------
# Bulk fetch — each model type
# ---------------------------------------------------------------------------


class TestBulkFetchSwitchport:
    def test_switchport_fetched_per_serial(self):
        p1 = port(serial="S1", port_id="1")
        p2 = port(serial="S1", port_id="2")
        p3 = port(serial="S2", port_id="1")

        live_p1 = port(serial="S1", port_id="1")
        live_p2 = port(serial="S1", port_id="2")
        live_p3 = port(serial="S2", port_id="1")

        def mock_get(serial, source):
            if serial == "S1":
                return [live_p1, live_p2]
            return [live_p3]

        with patch("merakisync.models.switchport.Switchport.get", side_effect=mock_get) as mock:
            lookup, failed = _bulk_fetch_model(Switchport, [p1, p2, p3], ORG)

        assert mock.call_count == 2
        assert len(lookup) == 3
        assert len(failed) == 0

    def test_switchport_fetch_failure_marks_serial_group(self):
        p1 = port(serial="S1", port_id="1")
        p2 = port(serial="S1", port_id="2")

        with patch("merakisync.models.switchport.Switchport.get", side_effect=Exception("err")):
            lookup, failed = _bulk_fetch_model(Switchport, [p1, p2], ORG)

        assert len(lookup) == 0
        assert _pk_key(p1) in failed
        assert _pk_key(p2) in failed


class TestBulkFetchVlan:
    def test_vlan_fetched_per_network(self):
        v1 = vlan_obj(network_id="N_1", vlan_id=100)
        v2 = vlan_obj(network_id="N_1", vlan_id=200)
        v3 = vlan_obj(network_id="N_2", vlan_id=100)

        def mock_get(network_id, source):
            if network_id == "N_1":
                return [v1, v2]
            return [v3]

        with patch("merakisync.models.vlan.Vlan.get", side_effect=mock_get) as mock:
            lookup, failed = _bulk_fetch_model(Vlan, [v1, v2, v3], ORG)

        assert mock.call_count == 2
        assert len(lookup) == 3

    def test_vlan_fetch_failure(self):
        v1 = vlan_obj(network_id="N_1")
        with patch("merakisync.models.vlan.Vlan.get", side_effect=Exception("err")):
            lookup, failed = _bulk_fetch_model(Vlan, [v1], ORG)
        assert _pk_key(v1) in failed


class TestBulkFetchNetwork:
    def test_network_fetched_org_wide(self):
        n1 = net(nid="N_1")
        n2 = net(nid="N_2")
        with patch("merakisync.models.network.Network.get", return_value=[n1, n2]) as mock:
            lookup, failed = _bulk_fetch_model(Network, [n1, n2], ORG)
        mock.assert_called_once_with(ORG, source="meraki")
        assert len(lookup) == 2
        assert len(failed) == 0

    def test_network_fetch_failure(self):
        n1 = net()
        with patch("merakisync.models.network.Network.get", side_effect=Exception("err")):
            lookup, failed = _bulk_fetch_model(Network, [n1], ORG)
        assert _pk_key(n1) in failed


class TestBulkFetchDevice:
    def test_device_fetched_org_wide(self):
        d1 = dev(serial="S1")
        d2 = dev(serial="S2")
        with patch("merakisync.models.device.Device.get", return_value=[d1, d2]) as mock:
            lookup, failed = _bulk_fetch_model(Device, [d1, d2], ORG)
        mock.assert_called_once_with(ORG, source="meraki")
        assert len(lookup) == 2

    def test_device_fetch_failure(self):
        d = dev()
        with patch("merakisync.models.device.Device.get", side_effect=Exception("err")):
            lookup, failed = _bulk_fetch_model(Device, [d], ORG)
        assert _pk_key(d) in failed


class TestBulkFetchOrganization:
    def test_organization_fetched_globally(self):
        o = org_obj()
        with patch("merakisync.models.organization.Organization.get", return_value=[o]) as mock:
            lookup, failed = _bulk_fetch_model(Organization, [o], ORG)
        mock.assert_called_once_with(source="meraki")
        assert len(lookup) == 1

    def test_organization_fetch_failure(self):
        o = org_obj()
        with patch("merakisync.models.organization.Organization.get", side_effect=Exception("err")):
            lookup, failed = _bulk_fetch_model(Organization, [o], ORG)
        assert _pk_key(o) in failed


class TestBulkFetchSsid:
    def test_ssid_fetched_per_network(self):
        s1 = ssid_obj(network_id="N_1", number=0)
        s2 = ssid_obj(network_id="N_1", number=1)
        s3 = ssid_obj(network_id="N_2", number=0)

        def mock_get(network_id, source):
            if network_id == "N_1":
                return [s1, s2]
            return [s3]

        with patch("merakisync.models.ssid.Ssid.get", side_effect=mock_get) as mock:
            lookup, failed = _bulk_fetch_model(Ssid, [s1, s2, s3], ORG)

        assert mock.call_count == 2
        assert len(lookup) == 3

    def test_ssid_fetch_failure(self):
        s = ssid_obj()
        with patch("merakisync.models.ssid.Ssid.get", side_effect=Exception("err")):
            lookup, failed = _bulk_fetch_model(Ssid, [s], ORG)
        assert _pk_key(s) in failed


class TestBulkFetchL3FirewallRule:
    def test_l3_rules_fetched_per_network(self):
        r1 = rule(network_id="N_1", rule_order=1)
        r2 = rule(network_id="N_1", rule_order=2)
        r3 = rule(network_id="N_2", rule_order=1)

        def mock_get(network_id, source):
            if network_id == "N_1":
                return [r1, r2]
            return [r3]

        with patch("merakisync.models.l3_firewall_rule.L3FirewallRule.get", side_effect=mock_get) as mock:
            lookup, failed = _bulk_fetch_model(L3FirewallRule, [r1, r2, r3], ORG)

        assert mock.call_count == 2
        assert len(lookup) == 3

    def test_l3_rule_fetch_failure(self):
        r = rule()
        with patch("merakisync.models.l3_firewall_rule.L3FirewallRule.get", side_effect=Exception("err")):
            lookup, failed = _bulk_fetch_model(L3FirewallRule, [r], ORG)
        assert _pk_key(r) in failed


class TestBulkFetchDhcpServerPolicy:
    def test_dhcp_policy_fetched_per_network(self):
        p1 = dhcp(network_id="N_1")
        p2 = dhcp(network_id="N_2")

        def mock_get(network_id, source):
            if network_id == "N_1":
                return p1
            return p2

        with patch("merakisync.models.dhcp_server_policy.DhcpServerPolicy.get", side_effect=mock_get) as mock:
            lookup, failed = _bulk_fetch_model(DhcpServerPolicy, [p1, p2], ORG)

        assert mock.call_count == 2
        assert len(lookup) == 2

    def test_dhcp_policy_none_returned_not_added_to_lookup(self):
        p = dhcp()
        with patch("merakisync.models.dhcp_server_policy.DhcpServerPolicy.get", return_value=None):
            lookup, failed = _bulk_fetch_model(DhcpServerPolicy, [p], ORG)
        assert len(lookup) == 0
        assert len(failed) == 0

    def test_dhcp_policy_fetch_failure(self):
        p = dhcp()
        with patch("merakisync.models.dhcp_server_policy.DhcpServerPolicy.get", side_effect=Exception("err")):
            lookup, failed = _bulk_fetch_model(DhcpServerPolicy, [p], ORG)
        assert _pk_key(p) in failed


# ---------------------------------------------------------------------------
# verify_many — multiple orgs
# ---------------------------------------------------------------------------


class TestVerifyManyMultiOrg:
    def test_batches_from_different_orgs_handled_independently(self):
        v1 = vlan_obj(network_id="N_1")
        v1.name = "updated"
        action1 = Action.update(v1)
        batch1 = ActionBatch(org_id="org_A", actions=[action1])
        batch1.id = "b1"

        v2 = vlan_obj(network_id="N_2")
        v2.name = "updated"
        action2 = Action.update(v2)
        batch2 = ActionBatch(org_id="org_B", actions=[action2])
        batch2.id = "b2"

        live_v1 = vlan_obj(network_id="N_1", name="updated")
        live_v2 = vlan_obj(network_id="N_2", name="updated")

        def mock_vlan_get(network_id, source):
            if network_id == "N_1":
                return [live_v1]
            return [live_v2]

        with patch("merakisync.models.vlan.Vlan.get", side_effect=mock_vlan_get):
            results = ActionBatch.verify_many([batch1, batch2])

        assert action1 in results[batch1].verified
        assert action2 in results[batch2].verified


# ---------------------------------------------------------------------------
# VerifyResult and Mismatch dataclasses
# ---------------------------------------------------------------------------


class TestVerifyResultRepr:
    def test_repr_shows_counts(self):
        from merakiops.action import Action as A
        actions = [A(resource="/x", operation="update", body={})]
        r = VerifyResult(
            verified=actions,
            mismatched=[],
            unverifiable=[],
            batch_errors=["err1"],
        )
        s = repr(r)
        assert "verified=1" in s
        assert "mismatched=0" in s
        assert "unverifiable=0" in s
        assert "batch_errors=1" in s


class TestMismatchDataclass:
    def test_mismatch_stores_action_and_mismatches(self):
        action = Action(resource="/x", operation="update", body={"vlan": 200})
        m = Mismatch(action=action, mismatches={"vlan": {"expected": 200, "actual": 100}})
        assert m.action is action
        assert m.mismatches["vlan"]["expected"] == 200
