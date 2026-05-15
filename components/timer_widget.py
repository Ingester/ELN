"""
ELN App — TimerWidget
Three-phase timer UI component:
  idle      → grey, shows total duration, [Start] button
  running   → orange, countdown MM:SS, [Pause] [Reset] ✏️
  paused    → orange dimmed, [Resume] [Reset]
  overtime  → red background, +MM:SS count-up, [Confirm, next step]
  confirmed → green check, shows final overtime

Subscribes to TimerManager for live updates.
Calls back on confirm so StepCard can advance to next step.
"""

from __future__ import annotations
import asyncio
import flet as ft
from typing import Callable, Optional

from timer_manager import get_timer_manager, TimerState
from utils.inline_editor import get_editable_numbers

def _open_overlay(page, ctrl):
    """Open a dialog/snackbar compatible with flet 0.70+."""
    if ctrl not in page.overlay:
        page.overlay.append(ctrl)
    ctrl.open = True
    page.update()

def _close_overlay(page, ctrl):
    """Close a dialog/snackbar compatible with flet 0.70+."""
    ctrl.open = False
    page.update()




# ─────────────────────────────────────────────
# Colours
# ─────────────────────────────────────────────
_C_IDLE      = ft.Colors.GREY_400
_C_RUNNING   = ft.Colors.ORANGE_600
_C_PAUSED    = ft.Colors.ORANGE_200
_C_OVERTIME  = ft.Colors.RED_600
_C_CONFIRMED = ft.Colors.GREEN_600
_C_BG_OVER   = ft.Colors.RED_50


def _fmt(seconds: int) -> str:
    """Format seconds as MM:SS."""
    s = abs(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


class TimerWidget(ft.Container):
    """
    Self-contained timer widget.
    experiment_id / step_id identify which timer in TimerManager.
    total_seconds: initial duration (may be overridden by user).
    on_confirm: called when user clicks 'Confirm, next step'.
    is_mobile: affects edit dialog style.
    """

    def __init__(
        self,
        experiment_id: int,
        step_id: int,
        total_seconds: int,
        on_confirm: Optional[Callable[[], None]] = None,
        is_mobile: bool = True,
    ):
        super().__init__(padding=12, border_radius=8)
        self.experiment_id = experiment_id
        self.step_id = step_id
        self.total_seconds = total_seconds
        self.on_confirm = on_confirm
        self.is_mobile = is_mobile

        self._tm = get_timer_manager()
        self._state: Optional[TimerState] = None
        self._mounted = False

        # Ensure timer exists in manager
        existing = self._tm.get_state(experiment_id, step_id)
        if existing is None:
            self._state = self._tm.create_or_restore(
                experiment_id, step_id, total_seconds
            )
        else:
            self._state = existing
            self.total_seconds = existing.total_seconds

        # Build sub-controls
        self._display = ft.Text(
            _fmt(self.total_seconds),
            size=36, weight=ft.FontWeight.BOLD,
            color=_C_IDLE,
        )
        self._status_label = ft.Text("", size=12, color=ft.Colors.GREY_600)
        self._btn_start   = ft.ElevatedButton("开始", on_click=self._on_start,
                                               bgcolor=ft.Colors.ORANGE_600,
                                               color=ft.Colors.WHITE)
        self._btn_pause   = ft.ElevatedButton("暂停", on_click=self._on_pause,
                                               visible=False)
        self._btn_resume  = ft.ElevatedButton("继续", on_click=self._on_resume,
                                               visible=False)
        self._btn_reset   = ft.TextButton("重置", on_click=self._on_reset,
                                           visible=False)
        self._btn_edit    = ft.IconButton(
            ft.Icons.EDIT_OUTLINED, tooltip="修改时长",
            on_click=self._on_edit, icon_color=ft.Colors.ORANGE_400,
        )
        self._btn_confirm = ft.ElevatedButton(
            "确认，继续下一步",
            on_click=self._on_confirm_click,
            bgcolor=ft.Colors.RED_600, color=ft.Colors.WHITE,
            visible=False,
        )

        self.content = ft.Column([
            ft.Row([
                self._display,
                self._btn_edit,
            ], alignment=ft.MainAxisAlignment.CENTER,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self._status_label,
            ft.Row([
                self._btn_start,
                self._btn_pause,
                self._btn_resume,
                self._btn_reset,
            ], alignment=ft.MainAxisAlignment.CENTER),
            self._btn_confirm,
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=8)

        # Render current state
        self._refresh_ui()

    def did_mount(self) -> None:
        # Refresh from the page task; direct thread callbacks are unreliable in Web.
        self._mounted = True
        self.page.run_task(self._refresh_loop)

    def did_unmount(self) -> None:
        self._mounted = False

    async def _refresh_loop(self) -> None:
        """Web-safe refresh loop; avoids relying only on background thread UI pushes."""
        while self._mounted:
            state = self._tm.get_state(self.experiment_id, self.step_id)
            if state is not None:
                self._state = state
                self._refresh_ui()
                try:
                    self.page.update()
                except Exception:
                    pass
            await asyncio.sleep(1)

    # ── Timer callbacks ────────────────────────

    def _on_tick(self, state: TimerState) -> None:
        self._state = state
        self._refresh_ui()
        try:
            self.update()
        except Exception:
            pass

    # ── Button handlers ────────────────────────

    def _on_start(self, _) -> None:
        self._state = self._tm.start_timer(self.experiment_id, self.step_id)
        self._refresh_ui()
        self.update()

    def _on_pause(self, _) -> None:
        self._state = self._tm.pause_timer(self.experiment_id, self.step_id)
        self._refresh_ui()
        self.update()

    def _on_resume(self, _) -> None:
        self._state = self._tm.start_timer(self.experiment_id, self.step_id)
        self._refresh_ui()
        self.update()

    def _on_reset(self, _) -> None:
        self._state = self._tm.reset_timer(self.experiment_id, self.step_id)
        self._refresh_ui()
        self.update()

    def _on_confirm_click(self, _) -> None:
        try:
            from notifications import stop_alert_sound
            stop_alert_sound()
        except Exception:
            pass
        self._state = self._tm.confirm_overtime(self.experiment_id, self.step_id)
        self._refresh_ui()
        self.update()
        if self.on_confirm:
            self.on_confirm()

    def _on_edit(self, _) -> None:
        """Open edit dialog: modify total duration (idle/paused) or remaining (running)."""
        state = self._state
        if state is None or state.status in ("overtime", "confirmed"):
            return

        if state.status in ("idle", "paused"):
            current_val = str(state.total_seconds // 60)
            label = "修改总时长（分钟）"
            hint = "输入分钟数"
        else:  # running
            current_val = str(state.remaining_seconds // 60)
            label = "修改剩余时间（分钟）"
            hint = "输入剩余分钟数"

        tf = ft.TextField(
            value=current_val, label=label, hint_text=hint,
            keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True, width=200,
        )

        def _save(_):
            try:
                minutes = float(tf.value or "0")
                new_secs = int(minutes * 60)
                if state.status in ("idle", "paused"):
                    self._state = self._tm.set_total_seconds(
                        self.experiment_id, self.step_id, new_secs)
                else:
                    self._state = self._tm.set_remaining_seconds(
                        self.experiment_id, self.step_id, new_secs)
                self._refresh_ui()
                self.update()
                dlg.open = False
                self.page.update()
            except ValueError:
                tf.error_text = "请输入有效数字"
                self.page.update()

        dlg = ft.AlertDialog(
            title=ft.Text(label),
            content=tf,
            actions=[
                ft.TextButton("取消", on_click=lambda _: self._close_dlg(dlg)),
                ft.ElevatedButton("确定", on_click=_save),
            ],
        )
        _open_overlay(self.page, dlg)
        dlg.open = True
        self.page.update()

    def _close_dlg(self, dlg: ft.AlertDialog) -> None:
        dlg.open = False
        self.page.update()

    # ── UI refresh ─────────────────────────────

    def _refresh_ui(self) -> None:
        state = self._state
        if state is None:
            return

        status = state.status

        if status == "idle":
            self._display.value = _fmt(state.total_seconds)
            self._display.color = _C_IDLE
            self._status_label.value = f"计划时长 {_fmt(state.total_seconds)}"
            self.bgcolor = None
            self._btn_start.visible = True
            self._btn_pause.visible = False
            self._btn_resume.visible = False
            self._btn_reset.visible = False
            self._btn_edit.visible = True
            self._btn_confirm.visible = False

        elif status == "running":
            self._display.value = _fmt(state.remaining_seconds)
            self._display.color = _C_RUNNING
            self._status_label.value = "计时中…"
            self.bgcolor = None
            self._btn_start.visible = False
            self._btn_pause.visible = True
            self._btn_resume.visible = False
            self._btn_reset.visible = True
            self._btn_edit.visible = True
            self._btn_confirm.visible = False

        elif status == "paused":
            self._display.value = _fmt(state.remaining_seconds)
            self._display.color = _C_PAUSED
            self._status_label.value = "已暂停"
            self.bgcolor = None
            self._btn_start.visible = False
            self._btn_pause.visible = False
            self._btn_resume.visible = True
            self._btn_reset.visible = True
            self._btn_edit.visible = True
            self._btn_confirm.visible = False

        elif status == "overtime":
            self._display.value = f"+{_fmt(state.overtime_seconds)}"
            self._display.color = _C_OVERTIME
            self._status_label.value = "✅ 时间到！正在超时计时…"
            self.bgcolor = _C_BG_OVER
            self._btn_start.visible = False
            self._btn_pause.visible = False
            self._btn_resume.visible = False
            self._btn_reset.visible = False
            self._btn_edit.visible = False
            self._btn_confirm.visible = True

        elif status == "confirmed":
            overtime = state.overtime_seconds
            if overtime > 0:
                self._display.value = f"+{_fmt(overtime)}"
                self._status_label.value = f"已确认，超时 {_fmt(overtime)}"
            else:
                self._display.value = "✅"
                self._status_label.value = "按时完成"
            self._display.color = _C_CONFIRMED
            self.bgcolor = None
            self._btn_start.visible = False
            self._btn_pause.visible = False
            self._btn_resume.visible = False
            self._btn_reset.visible = False
            self._btn_edit.visible = False
            self._btn_confirm.visible = False
