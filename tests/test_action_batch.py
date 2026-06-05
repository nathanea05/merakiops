"""Unit tests for ActionBatch lifecycle methods — from_actions, create, confirm,
status, wait_until_complete, wait_for_all, run, and __repr__."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from merakisync.models.switchport import Switchport

from merakiops.action import Action
from merakiops.action_batch import ActionBatch
from merakiops.result import VerifyResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_port_action(serial: str = "Q2AB", port_id: str = "1", vlan: int = 200) -> Action:
    port = Switchport(serial=serial, port_id=port_id, name="p", vlan=vlan - 1)
    port.vlan = vlan
    return Action.update(port)



def submitted_batch(org_id: str = "123456", actions: list | None = None) -> ActionBatch:
    """Return a batch that has already been submitted (id is set)."""
    acts = actions or [make_port_action()]
    batch = ActionBatch(org_id=org_id, actions=acts)
    batch.id = "batch_001"
    return batch


def _mock_dashboard(
    batch_id: str = "batch_001",
    completed: bool = False,
    failed: bool = False,
    errors: list | None = None,
) -> MagicMock:
    """Return a mock dashboard whose action batch methods return sensible defaults."""
    dashboard = MagicMock()
    response = {
        "id": batch_id,
        "status": {
            "completed": completed,
            "failed": failed,
            "errors": errors or [],
        },
        "createdResources": [],
    }
    dashboard.organizations.createOrganizationActionBatch.return_value = response
    dashboard.organizations.updateOrganizationActionBatch.return_value = response
    dashboard.organizations.getOrganizationActionBatch.return_value = response
    return dashboard


# ---------------------------------------------------------------------------
# from_actions()
# ---------------------------------------------------------------------------


class TestFromActions:
    def test_single_action_accepted(self):
        action = make_port_action()
        batches = ActionBatch.from_actions("123456", action)
        assert len(batches) == 1
        assert batches[0].actions == [action]

    def test_list_of_actions(self):
        actions = [make_port_action(port_id=str(i)) for i in range(5)]
        batches = ActionBatch.from_actions("123456", actions)
        assert len(batches) == 1
        assert len(batches[0].actions) == 5

    def test_empty_actions_raises(self):
        with pytest.raises(ValueError, match="actions cannot be empty"):
            ActionBatch.from_actions("123456", [])

    def test_splits_at_100_for_async(self):
        actions = [make_port_action(port_id=str(i)) for i in range(150)]
        batches = ActionBatch.from_actions("123456", actions)
        assert len(batches) == 2
        assert len(batches[0].actions) == 100
        assert len(batches[1].actions) == 50

    def test_splits_at_20_for_synchronous(self):
        actions = [make_port_action(port_id=str(i)) for i in range(45)]
        batches = ActionBatch.from_actions("123456", actions, synchronous=True, confirmed=True)
        assert len(batches) == 3
        assert len(batches[0].actions) == 20
        assert len(batches[1].actions) == 20
        assert len(batches[2].actions) == 5

    def test_synchronous_without_confirmed_raises(self):
        actions = [make_port_action()]
        with pytest.raises(ValueError, match="confirmed=True"):
            ActionBatch.from_actions("123456", actions, synchronous=True, confirmed=False)

    def test_org_id_propagated(self):
        batches = ActionBatch.from_actions("org_abc", [make_port_action()])
        assert batches[0].org_id == "org_abc"

    def test_confirmed_default_is_false(self):
        batches = ActionBatch.from_actions("123456", [make_port_action()])
        assert batches[0].confirmed is False

    def test_synchronous_default_is_false(self):
        batches = ActionBatch.from_actions("123456", [make_port_action()])
        assert batches[0].synchronous is False

    def test_callback_propagated(self):
        cb = {"url": "https://webhook.example.com"}
        batches = ActionBatch.from_actions("123456", [make_port_action()], callback=cb)
        assert batches[0].callback == cb

    def test_exactly_100_actions_is_one_batch(self):
        actions = [make_port_action(port_id=str(i)) for i in range(100)]
        batches = ActionBatch.from_actions("123456", actions)
        assert len(batches) == 1

    def test_exactly_20_synchronous_actions_is_one_batch(self):
        actions = [make_port_action(port_id=str(i)) for i in range(20)]
        batches = ActionBatch.from_actions("123456", actions, synchronous=True, confirmed=True)
        assert len(batches) == 1


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------


class TestCreate:
    def test_create_sets_id(self):
        batch = ActionBatch(org_id="123456", actions=[make_port_action()])
        dashboard = _mock_dashboard(batch_id="abc123")
        batch.create(dashboard=dashboard, sleep_seconds=0)
        assert batch.id == "abc123"

    def test_create_sets_status_fields(self):
        batch = ActionBatch(org_id="123456", actions=[make_port_action()])
        dashboard = _mock_dashboard(batch_id="abc123", completed=True)
        batch.create(dashboard=dashboard, sleep_seconds=0)
        assert batch.completed is True
        assert batch.failed is False
        assert batch.errors == []

    def test_create_already_submitted_raises(self):
        batch = submitted_batch()
        with pytest.raises(RuntimeError, match="already been submitted"):
            batch.create(dashboard=MagicMock(), sleep_seconds=0)

    def test_create_calls_dashboard_with_correct_payload(self):
        action = make_port_action()
        batch = ActionBatch(org_id="123456", actions=[action])
        dashboard = _mock_dashboard()
        batch.create(dashboard=dashboard, sleep_seconds=0)

        dashboard.organizations.createOrganizationActionBatch.assert_called_once_with(
            "123456",
            confirmed=False,
            synchronous=False,
            actions=[action.to_meraki_action()],
        )

    def test_create_includes_callback_when_set(self):
        action = make_port_action()
        cb = {"url": "https://x.example.com"}
        batch = ActionBatch(org_id="123456", actions=[action], callback=cb)
        dashboard = _mock_dashboard()
        batch.create(dashboard=dashboard, sleep_seconds=0)

        _, kwargs = dashboard.organizations.createOrganizationActionBatch.call_args
        assert kwargs.get("callback") == cb or "callback" in str(dashboard.organizations.createOrganizationActionBatch.call_args)

    def test_create_omits_callback_when_none(self):
        action = make_port_action()
        batch = ActionBatch(org_id="123456", actions=[action], callback=None)
        dashboard = _mock_dashboard()
        batch.create(dashboard=dashboard, sleep_seconds=0)

        _, kwargs = dashboard.organizations.createOrganizationActionBatch.call_args
        assert "callback" not in kwargs

    def test_create_sleeps_by_default(self):
        batch = ActionBatch(org_id="123456", actions=[make_port_action()])
        dashboard = _mock_dashboard()
        with patch("merakiops.action_batch.time.sleep") as mock_sleep:
            batch.create(dashboard=dashboard, sleep_seconds=5.0)
        mock_sleep.assert_called_once_with(5.0)

    def test_create_sleep_zero_does_not_sleep(self):
        batch = ActionBatch(org_id="123456", actions=[make_port_action()])
        dashboard = _mock_dashboard()
        with patch("merakiops.action_batch.time.sleep") as mock_sleep:
            batch.create(dashboard=dashboard, sleep_seconds=0)
        mock_sleep.assert_not_called()

    def test_create_raises_on_api_error(self):
        batch = ActionBatch(org_id="123456", actions=[make_port_action()])
        dashboard = MagicMock()
        dashboard.organizations.createOrganizationActionBatch.side_effect = Exception("API error")
        with pytest.raises(Exception, match="API error"):
            batch.create(dashboard=dashboard, sleep_seconds=0)

    def test_create_fetches_dashboard_when_none(self):
        batch = ActionBatch(org_id="123456", actions=[make_port_action()])
        mock_db = _mock_dashboard()
        with patch("merakisync.dashboard.get_dashboard", return_value=mock_db):
            batch.create(sleep_seconds=0)
        assert batch.id is not None

    def test_create_returns_self(self):
        batch = ActionBatch(org_id="123456", actions=[make_port_action()])
        result = batch.create(dashboard=_mock_dashboard(), sleep_seconds=0)
        assert result is batch


# ---------------------------------------------------------------------------
# confirm()
# ---------------------------------------------------------------------------


class TestConfirm:
    def test_confirm_not_submitted_raises(self):
        batch = ActionBatch(org_id="123456", actions=[make_port_action()])
        with pytest.raises(RuntimeError, match="not been submitted yet"):
            batch.confirm(dashboard=MagicMock())

    def test_confirm_already_confirmed_is_noop(self):
        batch = submitted_batch()
        batch.confirmed = True
        dashboard = MagicMock()
        batch.confirm(dashboard=dashboard)
        dashboard.organizations.updateOrganizationActionBatch.assert_not_called()

    def test_confirm_sets_confirmed_true(self):
        batch = submitted_batch()
        batch.confirmed = False
        dashboard = _mock_dashboard()
        batch.confirm(dashboard=dashboard)
        assert batch.confirmed is True

    def test_confirm_calls_dashboard(self):
        batch = submitted_batch()
        batch.confirmed = False
        dashboard = _mock_dashboard()
        batch.confirm(dashboard=dashboard)
        dashboard.organizations.updateOrganizationActionBatch.assert_called_once_with(
            "123456", "batch_001", confirmed=True
        )

    def test_confirm_updates_status_fields(self):
        batch = submitted_batch()
        batch.confirmed = False
        dashboard = _mock_dashboard(completed=True)
        batch.confirm(dashboard=dashboard)
        assert batch.completed is True

    def test_confirm_raises_on_api_error(self):
        batch = submitted_batch()
        batch.confirmed = False
        dashboard = MagicMock()
        dashboard.organizations.updateOrganizationActionBatch.side_effect = Exception("err")
        with pytest.raises(Exception, match="err"):
            batch.confirm(dashboard=dashboard)

    def test_confirm_fetches_dashboard_when_none(self):
        batch = submitted_batch()
        batch.confirmed = False
        mock_db = _mock_dashboard()
        with patch("merakisync.dashboard.get_dashboard", return_value=mock_db):
            batch.confirm()
        assert batch.confirmed is True

    def test_confirm_returns_self(self):
        batch = submitted_batch()
        batch.confirmed = False
        result = batch.confirm(dashboard=_mock_dashboard())
        assert result is batch


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_not_submitted_raises(self):
        batch = ActionBatch(org_id="123456", actions=[make_port_action()])
        with pytest.raises(RuntimeError, match="not been submitted yet"):
            batch.status(dashboard=MagicMock())

    def test_status_returns_status_dict(self):
        batch = submitted_batch()
        dashboard = _mock_dashboard(completed=True)
        result = batch.status(dashboard=dashboard)
        assert result == {"completed": True, "failed": False, "errors": []}

    def test_status_updates_instance_fields(self):
        batch = submitted_batch()
        dashboard = _mock_dashboard(completed=True, errors=["action 1 failed"])
        batch.status(dashboard=dashboard)
        assert batch.completed is True
        assert batch.errors == ["action 1 failed"]

    def test_status_calls_correct_api_method(self):
        batch = submitted_batch()
        dashboard = _mock_dashboard()
        batch.status(dashboard=dashboard)
        dashboard.organizations.getOrganizationActionBatch.assert_called_once_with(
            "123456", "batch_001"
        )

    def test_status_raises_on_api_error(self):
        batch = submitted_batch()
        dashboard = MagicMock()
        dashboard.organizations.getOrganizationActionBatch.side_effect = Exception("err")
        with pytest.raises(Exception, match="err"):
            batch.status(dashboard=dashboard)

    def test_status_fetches_dashboard_when_none(self):
        batch = submitted_batch()
        mock_db = _mock_dashboard()
        with patch("merakisync.dashboard.get_dashboard", return_value=mock_db):
            result = batch.status()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# wait_until_complete()
# ---------------------------------------------------------------------------


class TestWaitUntilComplete:
    def test_not_submitted_raises(self):
        batch = ActionBatch(org_id="123456", actions=[make_port_action()])
        with pytest.raises(RuntimeError, match="not been submitted yet"):
            batch.wait_until_complete()

    def test_synchronous_batch_returns_immediately(self):
        batch = submitted_batch()
        batch.synchronous = True
        with patch("merakiops.action_batch.time.sleep") as mock_sleep:
            result = batch.wait_until_complete()
        mock_sleep.assert_not_called()
        assert result is True

    def test_synchronous_batch_with_errors_returns_false(self):
        batch = submitted_batch()
        batch.synchronous = True
        batch.errors = ["failed"]
        result = batch.wait_until_complete()
        assert result is False

    def test_already_completed_returns_immediately(self):
        batch = submitted_batch()
        batch.completed = True
        with patch("merakiops.action_batch.time.sleep") as mock_sleep:
            result = batch.wait_until_complete()
        mock_sleep.assert_not_called()
        assert result is True

    def test_already_failed_returns_false(self):
        batch = submitted_batch()
        batch.failed = True
        batch.errors = ["err"]
        result = batch.wait_until_complete()
        assert result is False

    def test_waits_until_complete(self):
        batch = submitted_batch()
        responses = [
            {"status": {"completed": False, "failed": False, "errors": []}},
            {"status": {"completed": False, "failed": False, "errors": []}},
            {"status": {"completed": True, "failed": False, "errors": []}},
        ]
        dashboard = MagicMock()
        dashboard.organizations.getOrganizationActionBatch.side_effect = responses
        with patch("merakiops.action_batch.time.sleep"):
            result = batch.wait_until_complete(dashboard=dashboard, poll_interval=0.1)
        assert result is True
        assert batch.completed is True

    def test_returns_false_when_completed_with_errors(self):
        batch = submitted_batch()
        dashboard = _mock_dashboard(completed=True, errors=["action failed"])
        with patch("merakiops.action_batch.time.sleep"):
            result = batch.wait_until_complete(dashboard=dashboard, poll_interval=0.1)
        assert result is False

    def test_timeout_raises(self):
        batch = submitted_batch()
        dashboard = _mock_dashboard(completed=False)
        with (
            patch("merakiops.action_batch.time.sleep"),
            patch("merakiops.action_batch.time.time", side_effect=[0, 0, 200, 200]),
        ):
            with pytest.raises(TimeoutError, match="did not complete"):
                batch.wait_until_complete(
                    dashboard=dashboard, timeout_seconds=100, poll_interval=1.0
                )


# ---------------------------------------------------------------------------
# wait_for_all()
# ---------------------------------------------------------------------------


class TestWaitForAll:
    def test_empty_batches_raises(self):
        with pytest.raises(ValueError, match="batches cannot be empty"):
            ActionBatch.wait_for_all([])

    def test_unsubmitted_batch_raises(self):
        unsubmitted = ActionBatch(org_id="123456", actions=[make_port_action()])
        with pytest.raises(RuntimeError, match="have not been submitted yet"):
            ActionBatch.wait_for_all([unsubmitted])

    def test_synchronous_batches_skipped(self):
        b1 = submitted_batch()
        b1.synchronous = True
        b1.completed = True
        with patch("merakiops.action_batch.time.sleep") as mock_sleep:
            result = ActionBatch.wait_for_all([b1])
        mock_sleep.assert_not_called()
        assert result[b1] is True

    def test_all_already_complete(self):
        b1 = submitted_batch()
        b1.completed = True
        b2 = submitted_batch()
        b2.completed = True
        result = ActionBatch.wait_for_all([b1, b2])
        assert result[b1] is True
        assert result[b2] is True

    def test_waits_for_pending_batches(self):
        b1 = submitted_batch()
        responses = [
            {"status": {"completed": False, "failed": False, "errors": []}},
            {"status": {"completed": True, "failed": False, "errors": []}},
        ]
        dashboard = MagicMock()
        dashboard.organizations.getOrganizationActionBatch.side_effect = responses
        with patch("merakiops.action_batch.time.sleep"):
            result = ActionBatch.wait_for_all([b1], dashboard=dashboard, poll_interval=0.01)
        assert result[b1] is True

    def test_failed_batch_returns_false(self):
        b1 = submitted_batch()
        responses = [
            {"status": {"completed": False, "failed": False, "errors": []}},
            {"status": {"completed": False, "failed": True, "errors": ["fail"]}},
        ]
        dashboard = MagicMock()
        dashboard.organizations.getOrganizationActionBatch.side_effect = responses
        with patch("merakiops.action_batch.time.sleep"):
            result = ActionBatch.wait_for_all([b1], dashboard=dashboard, poll_interval=0.01)
        assert result[b1] is False

    def test_timeout_raises(self):
        b1 = submitted_batch()
        dashboard = _mock_dashboard(completed=False)
        with (
            patch("merakiops.action_batch.time.sleep"),
            patch("merakiops.action_batch.time.time", side_effect=[0, 0, 200, 200]),
        ):
            with pytest.raises(TimeoutError, match="did not complete"):
                ActionBatch.wait_for_all([b1], dashboard=dashboard, timeout_seconds=100)

    def test_multiple_batches_different_completion_times(self):
        b1 = submitted_batch()
        b2 = submitted_batch()
        b2.id = "batch_002"

        call_count = {"n": 0}

        def side_effect(org_id, batch_id):
            call_count["n"] += 1
            if batch_id == "batch_001":
                return {"status": {"completed": True, "failed": False, "errors": []}}
            # b2 takes two polls
            if call_count["n"] <= 2:
                return {"status": {"completed": False, "failed": False, "errors": []}}
            return {"status": {"completed": True, "failed": False, "errors": []}}

        dashboard = MagicMock()
        dashboard.organizations.getOrganizationActionBatch.side_effect = side_effect

        with patch("merakiops.action_batch.time.sleep"):
            result = ActionBatch.wait_for_all([b1, b2], dashboard=dashboard, poll_interval=0.01)

        assert result[b1] is True
        assert result[b2] is True


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------


class TestRepr:
    def test_repr_not_submitted(self):
        batch = ActionBatch(org_id="123456", actions=[make_port_action()])
        assert "not submitted" in repr(batch)
        assert "123456" in repr(batch)

    def test_repr_submitted(self):
        batch = submitted_batch()
        assert "batch_001" in repr(batch)

    def test_repr_completed(self):
        batch = submitted_batch()
        batch.completed = True
        assert "completed" in repr(batch)

    def test_repr_failed(self):
        batch = submitted_batch()
        batch.failed = True
        assert "failed" in repr(batch)


# ---------------------------------------------------------------------------
# run() — integration across from_actions → create → confirm → wait → verify
# ---------------------------------------------------------------------------


class TestRun:
    def test_run_synchronous_confirmed_false_raises(self):
        with pytest.raises(ValueError, match="confirmed=True"):
            ActionBatch.run("123456", [make_port_action()], synchronous=True, confirmed=False)

    def test_run_empty_actions_raises(self):
        with pytest.raises(ValueError, match="actions cannot be empty"):
            ActionBatch.run("123456", [])

    def test_run_returns_combined_verify_result(self):
        actions = [make_port_action(port_id=str(i)) for i in range(3)]
        dashboard = _mock_dashboard(completed=True)

        fake_result = VerifyResult(
            verified=actions,
            mismatched=[],
            unverifiable=[],
            batch_errors=[],
        )

        with (
            patch("merakiops.action_batch.time.sleep"),
            patch(
                "merakiops.action_batch.ActionBatch.verify_many",
                side_effect=lambda batches: {b: fake_result for b in batches},
            ),
        ):
            result = ActionBatch.run("123456", actions, dashboard=dashboard)

        assert len(result.verified) == 3
        assert len(result.mismatched) == 0

    def test_run_combines_results_across_batches(self):
        # 150 actions → 2 batches; each batch verifies 75 actions
        actions = [make_port_action(port_id=str(i)) for i in range(150)]
        dashboard = _mock_dashboard(completed=True)

        def fake_verify(batches):
            per_batch = {}
            for i, b in enumerate(batches):
                per_batch[b] = VerifyResult(
                    verified=b.actions,
                    mismatched=[],
                    unverifiable=[],
                    batch_errors=[],
                )
            return per_batch

        with (
            patch("merakiops.action_batch.time.sleep"),
            patch("merakiops.action_batch.ActionBatch.verify_many", side_effect=fake_verify),
        ):
            result = ActionBatch.run("123456", actions, dashboard=dashboard)

        assert len(result.verified) == 150

    def test_run_confirm_called_for_each_batch(self):
        actions = [make_port_action()]
        dashboard = _mock_dashboard(completed=True)

        confirmed_calls = []
        original_confirm = ActionBatch.confirm

        def tracking_confirm(self, dashboard=None):
            confirmed_calls.append(self)
            self.confirmed = True
            return self

        with (
            patch("merakiops.action_batch.time.sleep"),
            patch.object(ActionBatch, "confirm", tracking_confirm),
            patch(
                "merakiops.action_batch.ActionBatch.verify_many",
                side_effect=lambda batches: {
                    b: VerifyResult(verified=[], mismatched=[], unverifiable=[], batch_errors=[])
                    for b in batches
                },
            ),
        ):
            ActionBatch.run("123456", actions, dashboard=dashboard)

        assert len(confirmed_calls) == 1
