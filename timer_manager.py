"""
ELN App — TimerManager
Global singleton managing all experiment timers.
Three-phase state machine: idle → running (countdown) → overtime (count-up) → confirmed

Runs a single background thread that ticks every second.
UI components subscribe via callbacks to receive state updates.
"""

from __future__ import annotations
import threading
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Timer state
# ─────────────────────────────────────────────

@dataclass
class TimerState:
    timer_id: str                   # "{experiment_id}_{step_id}"
    experiment_id: int
    step_id: int
    total_seconds: int              # original (or user-overridden) duration
    remaining_seconds: int          # countdown phase: counts down to 0
    overtime_seconds: int           # overtime phase: counts up from 0
    status: str                     # idle | running | paused | overtime | confirmed
    timer_finished_at: Optional[str] = None   # ISO timestamp when countdown hit 0
    started_at: Optional[str] = None

    @property
    def display_seconds(self) -> int:
        """Seconds to show in UI: remaining during countdown, overtime during count-up."""
        if self.status in ("overtime",):
            return self.overtime_seconds
        return self.remaining_seconds

    @property
    def is_active(self) -> bool:
        return self.status in ("running", "overtime")

    def clone(self) -> "TimerState":
        return TimerState(
            timer_id=self.timer_id,
            experiment_id=self.experiment_id,
            step_id=self.step_id,
            total_seconds=self.total_seconds,
            remaining_seconds=self.remaining_seconds,
            overtime_seconds=self.overtime_seconds,
            status=self.status,
            timer_finished_at=self.timer_finished_at,
            started_at=self.started_at,
        )


# ─────────────────────────────────────────────
# Callback type
# ─────────────────────────────────────────────

# Signature: (timer_state: TimerState) -> None
TimerCallback = Callable[[TimerState], None]

# Called when countdown hits zero: (timer_state) -> None
TimerFinishedCallback = Callable[[TimerState], None]


# ─────────────────────────────────────────────
# TimerManager singleton
# ─────────────────────────────────────────────

class TimerManager:
    _instance: Optional["TimerManager"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "TimerManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self._timers: dict[str, TimerState] = {}          # timer_id → state
        self._callbacks: dict[str, list[TimerCallback]] = {}  # timer_id → [cb]
        self._finished_callbacks: list[TimerFinishedCallback] = []
        self._state_lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ──────────────────────────────

    def start(self) -> None:
        """Start the background tick thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._tick_loop, daemon=True, name="timer-manager"
        )
        self._thread.start()
        logger.info("TimerManager started")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("TimerManager stopped")

    # ── Timer operations ───────────────────────

    def create_or_restore(
        self,
        experiment_id: int,
        step_id: int,
        total_seconds: int,
        remaining_seconds: Optional[int] = None,
        overtime_seconds: int = 0,
        status: str = "idle",
        timer_finished_at: Optional[str] = None,
        started_at: Optional[str] = None,
    ) -> TimerState:
        """Create a new timer or restore one from persisted state."""
        tid = f"{experiment_id}_{step_id}"
        with self._state_lock:
            state = TimerState(
                timer_id=tid,
                experiment_id=experiment_id,
                step_id=step_id,
                total_seconds=total_seconds,
                remaining_seconds=remaining_seconds if remaining_seconds is not None else total_seconds,
                overtime_seconds=overtime_seconds,
                status=status,
                timer_finished_at=timer_finished_at,
                started_at=started_at,
            )
            self._timers[tid] = state
        return state.clone()

    def start_timer(self, experiment_id: int, step_id: int) -> Optional[TimerState]:
        """Start or resume countdown."""
        tid = f"{experiment_id}_{step_id}"
        with self._state_lock:
            state = self._timers.get(tid)
            if state is None:
                return None
            if state.status in ("idle", "paused"):
                state.status = "running"
                if state.started_at is None:
                    state.started_at = _now()
                self._log_event_locked(state, "start")
        self._persist(tid)
        return self._timers[tid].clone()

    def pause_timer(self, experiment_id: int, step_id: int) -> Optional[TimerState]:
        tid = f"{experiment_id}_{step_id}"
        with self._state_lock:
            state = self._timers.get(tid)
            if state and state.status == "running":
                state.status = "paused"
                self._log_event_locked(state, "pause")
        self._persist(tid)
        return self._timers.get(tid, None) and self._timers[tid].clone()

    def reset_timer(self, experiment_id: int, step_id: int) -> Optional[TimerState]:
        """Reset countdown to full duration."""
        tid = f"{experiment_id}_{step_id}"
        with self._state_lock:
            state = self._timers.get(tid)
            if state:
                self._log_event_locked(state, "reset")
                state.remaining_seconds = state.total_seconds
                state.overtime_seconds = 0
                state.status = "idle"
                state.timer_finished_at = None
                state.started_at = None
        self._persist(tid)
        return self._timers.get(tid, None) and self._timers[tid].clone()

    def set_total_seconds(self, experiment_id: int, step_id: int,
                          new_total: int) -> Optional[TimerState]:
        """Edit total duration (before start or during countdown)."""
        tid = f"{experiment_id}_{step_id}"
        with self._state_lock:
            state = self._timers.get(tid)
            if state and state.status not in ("overtime", "confirmed"):
                state.total_seconds = new_total
                state.remaining_seconds = new_total
                self._log_event_locked(state, "override")
        self._persist(tid)
        return self._timers.get(tid, None) and self._timers[tid].clone()

    def set_remaining_seconds(self, experiment_id: int, step_id: int,
                               new_remaining: int) -> Optional[TimerState]:
        """Edit remaining time during active countdown."""
        tid = f"{experiment_id}_{step_id}"
        with self._state_lock:
            state = self._timers.get(tid)
            if state and state.status in ("running", "paused"):
                state.remaining_seconds = max(0, new_remaining)
        self._persist(tid)
        return self._timers.get(tid, None) and self._timers[tid].clone()

    def sync_timer(
        self,
        experiment_id: int,
        step_id: int,
        total_seconds: int,
        remaining_seconds: int,
        status: str,
        overtime_seconds: int = 0,
        action: str = "sync",
        elapsed_seconds: Optional[int] = None,
    ) -> TimerState:
        """Replace local state from a client-side authoritative timer."""
        tid = f"{experiment_id}_{step_id}"
        normalized_status = status if status in ("idle", "running", "paused", "overtime") else "idle"
        remaining = max(0, int(remaining_seconds))
        overtime = max(0, int(overtime_seconds))
        if normalized_status == "running" and remaining <= 0:
            normalized_status = "overtime"
            overtime = max(overtime, abs(int(remaining_seconds)))
        with self._state_lock:
            previous = self._timers.get(tid)
            should_log_action = bool(action and action not in ("sync", "tick"))
            if should_log_action and previous is not None:
                self._log_event_locked(previous, action, elapsed_seconds=elapsed_seconds)
            state = TimerState(
                timer_id=tid,
                experiment_id=experiment_id,
                step_id=step_id,
                total_seconds=max(0, int(total_seconds)),
                remaining_seconds=remaining,
                overtime_seconds=overtime,
                status=normalized_status,
                timer_finished_at=_now() if normalized_status == "overtime" else None,
                started_at=_now() if normalized_status == "running" else None,
            )
            self._timers[tid] = state
            if should_log_action and previous is None:
                self._log_event_locked(state, action, elapsed_seconds=elapsed_seconds)
        self._persist(tid)
        return state.clone()

    def confirm_overtime(self, experiment_id: int, step_id: int) -> Optional[TimerState]:
        """User clicks 'Confirm, next step' — freeze overtime count and mark confirmed."""
        tid = f"{experiment_id}_{step_id}"
        with self._state_lock:
            state = self._timers.get(tid)
            if state and state.status == "overtime":
                state.status = "confirmed"
                self._log_event_locked(state, "confirm")
        self._persist(tid)
        # Also persist overtime_seconds to steps table
        self._write_overtime_to_step(tid)
        return self._timers.get(tid, None) and self._timers[tid].clone()

    def get_state(self, experiment_id: int, step_id: int) -> Optional[TimerState]:
        tid = f"{experiment_id}_{step_id}"
        with self._state_lock:
            state = self._timers.get(tid)
        return state.clone() if state else None

    def get_all_states(self) -> list[TimerState]:
        with self._state_lock:
            return [s.clone() for s in self._timers.values()]

    # ── Subscriptions ──────────────────────────

    def subscribe(self, experiment_id: int, step_id: int,
                  callback: TimerCallback) -> None:
        """Subscribe to tick updates for a specific timer."""
        tid = f"{experiment_id}_{step_id}"
        with self._state_lock:
            if tid not in self._callbacks:
                self._callbacks[tid] = []
            if callback not in self._callbacks[tid]:
                self._callbacks[tid].append(callback)

    def unsubscribe(self, experiment_id: int, step_id: int,
                    callback: TimerCallback) -> None:
        tid = f"{experiment_id}_{step_id}"
        with self._state_lock:
            cbs = self._callbacks.get(tid, [])
            if callback in cbs:
                cbs.remove(callback)

    def subscribe_finished(self, callback: TimerFinishedCallback) -> None:
        """Subscribe to countdown-finished events (triggers notification)."""
        if callback not in self._finished_callbacks:
            self._finished_callbacks.append(callback)

    def unsubscribe_finished(self, callback: TimerFinishedCallback) -> None:
        if callback in self._finished_callbacks:
            self._finished_callbacks.remove(callback)

    # ── Background tick ────────────────────────

    def _tick_loop(self) -> None:
        while self._running:
            time.sleep(1)
            self._tick()

    def _tick(self) -> None:
        finished_this_tick: list[TimerState] = []

        with self._state_lock:
            for tid, state in self._timers.items():
                if state.status == "running":
                    if state.remaining_seconds > 0:
                        state.remaining_seconds -= 1
                        if state.remaining_seconds == 0:
                            # Transition to overtime
                            state.status = "overtime"
                            state.timer_finished_at = _now()
                            finished_this_tick.append(state.clone())
                    # remaining already 0 but status still running — shouldn't happen
                elif state.status == "overtime":
                    state.overtime_seconds += 1

        # Fire callbacks outside lock
        for tid, state in self._timers.items():
            if state.status in ("running", "overtime"):
                cbs = self._callbacks.get(tid, [])
                snap = state.clone()
                for cb in cbs:
                    try:
                        cb(snap)
                    except Exception as e:
                        logger.warning(f"Timer callback error: {e}")

        # Fire finished callbacks
        for snap in finished_this_tick:
            self._persist(snap.timer_id)
            for cb in self._finished_callbacks:
                try:
                    cb(snap)
                except Exception as e:
                    logger.warning(f"Timer finished callback error: {e}")

    # ── Persistence ────────────────────────────

    def _persist(self, tid: str) -> None:
        """Write timer state to DB (non-blocking, best-effort)."""
        try:
            import db.database as db_ops
            state = self._timers.get(tid)
            if state is None:
                return
            db_ops.upsert_timer(
                experiment_id=state.experiment_id,
                step_id=state.step_id,
                total_seconds=state.total_seconds,
                remaining_seconds=state.remaining_seconds,
                overtime_seconds=state.overtime_seconds,
                status=state.status,
                timer_finished_at=state.timer_finished_at,
                started_at=state.started_at,
            )
        except Exception as e:
            logger.warning(f"Timer persist error: {e}")

    def _write_overtime_to_step(self, tid: str) -> None:
        """On confirm, write overtime_seconds + timer_finished_at to steps table."""
        try:
            import db.database as db_ops
            state = self._timers.get(tid)
            if state is None:
                return
            db_ops.update_step(
                state.step_id,
                overtime_seconds=state.overtime_seconds,
                timer_finished_at=state.timer_finished_at,
            )
        except Exception as e:
            logger.warning(f"Step overtime write error: {e}")

    def _log_event_locked(
        self,
        state: TimerState,
        action: str,
        elapsed_seconds: Optional[int] = None,
        notes: str = "",
    ) -> None:
        """Append a timer operation while the caller already holds state lock."""
        try:
            import db.database as db_ops
            elapsed = _elapsed_for_state(state) if elapsed_seconds is None else elapsed_seconds
            db_ops.log_timer_event(
                experiment_id=state.experiment_id,
                step_id=state.step_id,
                action=action,
                total_seconds=state.total_seconds,
                remaining_seconds=state.remaining_seconds,
                overtime_seconds=state.overtime_seconds,
                elapsed_seconds=elapsed,
                notes=notes,
            )
        except Exception as e:
            logger.warning(f"Timer event log error: {e}")

    def restore_from_db(self) -> None:
        """On app start, restore any timers that were running/overtime."""
        try:
            import db.database as db_ops
            active = db_ops.list_active_timers()
            for t in active:
                # Recalculate elapsed time since last update
                elapsed = _elapsed_since(t.updated_at)
                if t.status == "running":
                    new_remaining = max(0, t.remaining_seconds - elapsed)
                    if new_remaining == 0:
                        # Transitioned to overtime while app was closed
                        overtime = elapsed - t.remaining_seconds
                        self.create_or_restore(
                            t.experiment_id, t.step_id, t.total_seconds,
                            remaining_seconds=0,
                            overtime_seconds=t.overtime_seconds + overtime,
                            status="overtime",
                            timer_finished_at=t.timer_finished_at or _now(),
                            started_at=t.started_at,
                        )
                    else:
                        self.create_or_restore(
                            t.experiment_id, t.step_id, t.total_seconds,
                            remaining_seconds=new_remaining,
                            overtime_seconds=0,
                            status="running",
                            timer_finished_at=None,
                            started_at=t.started_at,
                        )
                elif t.status == "overtime":
                    self.create_or_restore(
                        t.experiment_id, t.step_id, t.total_seconds,
                        remaining_seconds=0,
                        overtime_seconds=t.overtime_seconds + elapsed,
                        status="overtime",
                        timer_finished_at=t.timer_finished_at,
                        started_at=t.started_at,
                    )
            logger.info(f"Restored {len(active)} active timers from DB")
        except Exception as e:
            logger.warning(f"Timer restore error: {e}")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_since(iso_str: str) -> int:
    """Return integer seconds elapsed since an ISO timestamp."""
    try:
        then = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0, int((now - then).total_seconds()))
    except Exception:
        return 0


def _elapsed_for_state(state: TimerState) -> int:
    if state.status in ("overtime", "confirmed"):
        return max(0, int(state.total_seconds or 0)) + max(0, int(state.overtime_seconds or 0))
    return max(0, int(state.total_seconds or 0) - int(state.remaining_seconds or 0))


# Module-level singleton accessor
def get_timer_manager() -> TimerManager:
    return TimerManager()
