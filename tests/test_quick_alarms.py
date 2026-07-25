from datetime import datetime, timedelta, timezone

import db.database as db_ops
from alarm_manager import AlarmManager


def test_quick_alarm_persists_fires_and_dismisses(tmp_path):
    db_ops.init_db(str(tmp_path / "eln.db"))
    now = datetime.now(timezone.utc)
    due = now + timedelta(minutes=5)

    created = db_ops.create_quick_alarm(
        alarm_id="alarm-1",
        label="APOE plate check",
        kind="alarm",
        due_at=due.isoformat(),
    )
    assert created["status"] == "active"
    assert [item["id"] for item in db_ops.list_quick_alarms(["active"])] == ["alarm-1"]

    notified = []
    manager = AlarmManager(lambda label, experiment: notified.append((label, experiment)))
    assert manager.poll_once() == []

    claimed = db_ops.claim_due_quick_alarms((due + timedelta(seconds=1)).isoformat())
    assert [item["id"] for item in claimed] == ["alarm-1"]
    assert db_ops.get_quick_alarm("alarm-1")["status"] == "ringing"

    dismissed = db_ops.dismiss_quick_alarm("alarm-1")
    assert dismissed["status"] == "dismissed"
    assert db_ops.list_quick_alarms(["active", "ringing"]) == []


def test_alarm_manager_notifies_each_claimed_alarm_once(tmp_path):
    db_ops.init_db(str(tmp_path / "eln.db"))
    now = datetime.now(timezone.utc)
    db_ops.create_quick_alarm(
        alarm_id="alarm-2",
        label="Change medium",
        kind="timer",
        due_at=(now - timedelta(seconds=1)).isoformat(),
    )

    notified = []
    manager = AlarmManager(lambda label, experiment: notified.append((label, experiment)))
    claimed = manager.poll_once()

    assert [item["id"] for item in claimed] == ["alarm-2"]
    assert notified == [("Change medium", "")]
    assert manager.poll_once() == []
    assert notified == [("Change medium", "")]
