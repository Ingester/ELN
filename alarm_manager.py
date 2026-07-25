"""Persistent ELN quick-alarm scheduler.

The scheduler belongs to the ELN server process, so alarms remain active when
all browser tabs are closed. The Windows logon starter keeps that process alive.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import db.database as db_ops
from notifications import notify_alarm

logger = logging.getLogger(__name__)


class AlarmManager:
    def __init__(self, notifier: Callable[[str, str], None] = notify_alarm) -> None:
        self._notifier = notifier
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._loop,
                daemon=True,
                name="eln-alarm-manager",
            )
            self._thread.start()
        logger.info("AlarmManager started")

    def stop(self) -> None:
        with self._lock:
            self._running = False
            thread = self._thread
        if thread:
            thread.join(timeout=2)
        logger.info("AlarmManager stopped")

    def _loop(self) -> None:
        while self._running:
            try:
                self.poll_once()
            except Exception as exc:
                logger.warning("Alarm polling failed: %s", exc)
            threading.Event().wait(1)

    def poll_once(self) -> list[dict]:
        claimed = db_ops.claim_due_quick_alarms()
        for alarm in claimed:
            experiment_name = ""
            if alarm.get("experiment_id"):
                experiment = db_ops.get_experiment(int(alarm["experiment_id"]))
                experiment_name = experiment.name if experiment else ""
            try:
                self._notifier(alarm.get("label") or "闹钟", experiment_name)
            except Exception as exc:
                logger.warning("Alarm notification failed: %s", exc)
        return claimed


_manager = AlarmManager()


def get_alarm_manager() -> AlarmManager:
    return _manager
