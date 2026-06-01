"""
ELN App — Home View
Shows only active (in-progress) experiments.
Each card shows: name, current step progress, live timer status.
Empty state: prompt to open Protocol Library.
Top-right: history icon → navigate to history view.
"""

from __future__ import annotations
import asyncio
import os
from datetime import datetime, timezone
import flet as ft
from typing import Callable, Optional

from timer_manager import get_timer_manager, TimerState
from utils.app_settings import is_english
from utils.i18n import tr


def _debug_log(message: str) -> None:
    try:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "ui_debug.log"), "a", encoding="utf-8") as f:
            ts = datetime.now(timezone.utc).isoformat()
            f.write(f"{ts} home: {message}\n")
    except Exception:
        pass


def _get(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _open_overlay(page, ctrl):
    if ctrl not in page.overlay:
        page.overlay.append(ctrl)
    ctrl.open = True
    page.update()


def _close_overlay(page, ctrl):
    ctrl.open = False
    page.update()


def build_home_view(
    page: ft.Page,
    data_provider,                          # db.database or utils.api_client
    on_open_experiment: Callable[[int], None],   # navigate to stepper
    on_open_history: Callable[[], None],
    on_open_protocols: Callable[[], None],
    is_mobile: bool = True,
) -> ft.Control:
    """Build and return the Home view control."""

    tm = get_timer_manager()
    english = is_english()
    _timer_refs: dict[int, dict[str, ft.Control]] = {}
    _navigating = [False]
    status_text = ft.Text("", size=12, color=ft.Colors.ORANGE_700)

    # ── Load active experiments ─────────────────

    def _load_experiments():
        _timer_refs.clear()
        try:
            exps = data_provider.list_experiments(status="active")
            try:
                exps += data_provider.list_experiments(status="needs_wrapup")
            except Exception:
                pass
        except Exception as e:
            return [_error_card(str(e))]

        if not exps:
            return [_empty_state()]

        cards = []
        for exp in exps:
            cards.append(_build_experiment_card(exp))
        cards.append(
            ft.Container(
                content=ft.TextButton(
                    tr("+ 从协议库新建实验"),
                    on_click=lambda _: on_open_protocols(),
                    style=ft.ButtonStyle(color=ft.Colors.ORANGE_600),
                ),
                alignment=ft.Alignment.CENTER,
                padding=ft.Padding.symmetric(vertical=8),
            )
        )
        return cards

    def _build_experiment_card(exp) -> ft.Container:
        exp_id = _get(exp, "id")
        name = _get(exp, "name", "实验")
        status = _get(exp, "status", "active")
        progress = {}
        if not isinstance(exp, dict):
            try:
                progress = data_provider.get_experiment_progress(exp_id)
            except Exception:
                progress = {}
        total = _get(exp, "total_steps", progress.get("total_steps", 0))
        completed = _get(exp, "completed_steps", progress.get("completed_steps", 0))
        current_idx = _get(exp, "current_step_index", progress.get("current_step_index", 0))
        status_label = tr("待收尾") if status == "needs_wrapup" else tr("进行中")
        status_color = ft.Colors.BLUE_600 if status == "needs_wrapup" else ft.Colors.ORANGE_600

        # Timer status line
        timer_line = _build_timer_line(exp_id)

        progress_bar = ft.ProgressBar(
            value=completed / total if total > 0 else 0,
            color=ft.Colors.ORANGE_600,
            bgcolor=ft.Colors.GREY_200,
            height=4,
        )

        card_content = ft.Column([
            ft.Row([
                ft.Text(name, size=15, weight=ft.FontWeight.BOLD, expand=True),
                ft.Container(
                    content=ft.Text(status_label, size=11, color=ft.Colors.WHITE),
                    bgcolor=status_color,
                    border_radius=10,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                ),
            ]),
            ft.Text(
                f"Step {current_idx + 1}/{total}",
                size=12, color=ft.Colors.GREY_600,
            ),
            progress_bar,
            timer_line,
            ft.Row([
                ft.Container(expand=True),
                ft.TextButton(
                    tr("放弃实验"),
                    on_click=lambda _, e=exp: _confirm_abandon(e),
                    style=ft.ButtonStyle(color=ft.Colors.RED_500),
                ),
                ft.ElevatedButton(
                    tr("继续 →"),
                    url=ft.Url(
                        f"{_native_runner_url()}/run?experiment_id={int(exp_id)}",
                        target=ft.UrlTarget.SELF,
                    ),
                    bgcolor=ft.Colors.ORANGE_600,
                    color=ft.Colors.WHITE,
                    height=32,
                ),
            ]),
        ], spacing=6)

        return ft.Container(
            content=card_content,
            border=ft.Border.all(1, ft.Colors.GREY_200),
            border_radius=10,
            padding=14,
            margin=ft.Margin.only(bottom=10),
            bgcolor=ft.Colors.WHITE,
            shadow=ft.BoxShadow(
                spread_radius=0, blur_radius=4,
                color=ft.Colors.with_opacity(0.08, ft.Colors.BLACK),
                offset=ft.Offset(0, 2),
            ),
        )

    def _confirm_abandon(exp) -> None:
        exp_id = int(_get(exp, "id"))
        name = _get(exp, "name", "实验")

        def _do_abandon(_):
            _close_overlay(page, dlg)
            try:
                data_provider.update_experiment(exp_id, status="archived")
                status_text.value = tr("实验已放入历史记录")
                cards_column.controls = _load_experiments()
                page.update()
            except Exception as ex:
                status_text.value = f"{tr('放弃失败')}：{ex}"
                status_text.color = ft.Colors.RED_500
                page.update()

        dlg = ft.AlertDialog(
            title=ft.Text(tr("放弃实验")),
            content=ft.Text(f"{tr('确认放弃这个实验？')} {name}\n{tr('实验会进入历史记录，以后可以继续。')}"),
            actions=[
                ft.TextButton(tr("取消"), on_click=lambda _: _close_overlay(page, dlg)),
                ft.ElevatedButton(
                    tr("放弃"),
                    on_click=_do_abandon,
                    bgcolor=ft.Colors.RED_600,
                    color=ft.Colors.WHITE,
                ),
            ],
        )
        _open_overlay(page, dlg)

    def _native_runner_url() -> str:
        configured = os.environ.get("ELN_API_PUBLIC_URL", "").rstrip("/")
        if configured:
            return configured
        try:
            from server.startup import get_local_ip
            return f"http://{get_local_ip()}:8000"
        except Exception:
            return "http://127.0.0.1:8000"

    def _timer_payload(exp_id: int) -> dict:
        """Find any active timer for this experiment and return render data."""
        all_states = tm.get_all_states()
        active = [
            s for s in all_states
            if s.experiment_id == exp_id and s.status in ("running", "paused", "overtime")
        ]
        if not active:
            active = _active_timer_records_from_db(exp_id)
        if not active:
            return {
                "visible": False,
                "icon": ft.Icons.TIMER_OUTLINED,
                "text": "",
                "color": ft.Colors.GREY_600,
            }

        state = active[0]
        status = _get(state, "status", "idle")
        remaining_seconds = int(_get(state, "remaining_seconds", 0) or 0)
        overtime_seconds = int(_get(state, "overtime_seconds", 0) or 0)
        if status == "overtime":
            mins = overtime_seconds // 60
            secs = overtime_seconds % 60
            return {
                "visible": True,
                "icon": ft.Icons.ERROR_OUTLINE,
                "text": f"{'Overtime' if english else '超时中'}: +{mins:02d}:{secs:02d}",
                "color": ft.Colors.RED_600,
            }
        elif status == "paused":
            mins = remaining_seconds // 60
            secs = remaining_seconds % 60
            return {
                "visible": True,
                "icon": ft.Icons.PAUSE_CIRCLE_OUTLINE,
                "text": f"{'Paused' if english else '已暂停'} {mins:02d}:{secs:02d}",
                "color": ft.Colors.GREY_600,
            }
        else:
            mins = remaining_seconds // 60
            secs = remaining_seconds % 60
            return {
                "visible": True,
                "icon": ft.Icons.TIMER_OUTLINED,
                "text": f"{'Remaining' if english else '剩余'} {mins:02d}:{secs:02d}",
                "color": ft.Colors.ORANGE_600,
            }

    def _active_timer_records_from_db(exp_id: int) -> list[dict]:
        """Fallback for timers controlled by the native browser page."""
        if not hasattr(data_provider, "list_active_timers"):
            return []
        try:
            records = data_provider.list_active_timers()
        except Exception as ex:
            _debug_log(f"list_active_timers failed: {type(ex).__name__}: {ex}")
            return []
        payloads = []
        now = datetime.now(timezone.utc)
        for rec in records:
            if int(_get(rec, "experiment_id", 0) or 0) != int(exp_id):
                continue
            status = _get(rec, "status", "idle")
            remaining = int(_get(rec, "remaining_seconds", 0) or 0)
            overtime = int(_get(rec, "overtime_seconds", 0) or 0)
            updated_at = _parse_dt(_get(rec, "updated_at", ""))
            if updated_at and status == "running":
                elapsed = max(0, int((now - updated_at).total_seconds()))
                if elapsed >= remaining:
                    status = "overtime"
                    overtime = max(overtime, elapsed - remaining)
                    remaining = 0
                else:
                    remaining -= elapsed
            elif updated_at and status == "overtime":
                overtime += max(0, int((now - updated_at).total_seconds()))
            payloads.append({
                "experiment_id": _get(rec, "experiment_id"),
                "step_id": _get(rec, "step_id"),
                "status": status,
                "remaining_seconds": remaining,
                "overtime_seconds": overtime,
            })
        return payloads

    def _parse_dt(value: str):
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _build_timer_line(exp_id: int) -> ft.Control:
        payload = _timer_payload(exp_id)
        icon = ft.Icon(payload["icon"], size=14, color=payload["color"])
        text = ft.Text(
            payload["text"],
            size=12,
            color=payload["color"],
            weight=ft.FontWeight.W_500,
        )
        row = ft.Row([icon, text], spacing=4, visible=payload["visible"])
        _timer_refs[int(exp_id)] = {"row": row, "icon": icon, "text": text}
        return row

    def _refresh_timer_lines() -> None:
        for exp_id, refs in list(_timer_refs.items()):
            payload = _timer_payload(exp_id)
            refs["row"].visible = payload["visible"]
            refs["icon"].name = payload["icon"]
            refs["icon"].color = payload["color"]
            refs["text"].value = payload["text"]
            refs["text"].color = payload["color"]

    def _empty_state() -> ft.Container:
        return ft.Container(
            content=ft.Column([
                ft.Icon(ft.Icons.SCIENCE_OUTLINED, size=64,
                        color=ft.Colors.GREY_300),
                ft.Text(tr("暂无进行中的实验"), size=16,
                        color=ft.Colors.GREY_500,
                        text_align=ft.TextAlign.CENTER),
                ft.ElevatedButton(
                    tr("📄 打开协议库，开始新实验"),
                    on_click=lambda _: on_open_protocols(),
                    bgcolor=ft.Colors.ORANGE_600,
                    color=ft.Colors.WHITE,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16),
            alignment=ft.Alignment.CENTER,
            expand=True,
            padding=40,
        )

    def _error_card(msg: str) -> ft.Container:
        return ft.Container(
            content=ft.Text(f"{tr('加载失败')}：{msg}", color=ft.Colors.RED_400),
            padding=16,
        )

    # ── Assemble view ───────────────────────────

    cards_column = ft.Column(
        controls=_load_experiments(),
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        spacing=0,
    )

    def _refresh(_=None):
        cards_column.controls = _load_experiments()
        page.update()

    header = ft.Column([
        ft.Row([
            ft.Text(tr("实验室笔记"), size=20, weight=ft.FontWeight.BOLD),
            ft.Container(expand=True),
            ft.IconButton(
                ft.Icons.HISTORY,
                tooltip=tr("历史记录"),
                on_click=lambda _: on_open_history(),
                icon_color=ft.Colors.GREY_600,
            ),
            ft.IconButton(
                ft.Icons.REFRESH,
                tooltip=tr("刷新"),
                on_click=_refresh,
                icon_color=ft.Colors.GREY_600,
            ),
        ]),
        status_text,
    ], spacing=2)

    class HomeRoot(ft.Column):
        def __init__(self):
            super().__init__([
                ft.Container(content=header, padding=ft.Padding.symmetric(horizontal=16, vertical=8)),
                ft.Divider(height=1, color=ft.Colors.GREY_200),
                ft.Container(
                    content=cards_column,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                    expand=True,
                ),
            ], expand=True, spacing=0)
            self._mounted = False

        def did_mount(self) -> None:
            self._mounted = True
            self.page.run_task(self._refresh_loop)

        def did_unmount(self) -> None:
            self._mounted = False

        async def _refresh_loop(self) -> None:
            while self._mounted:
                try:
                    if not _navigating[0]:
                        _refresh_timer_lines()
                        for refs in list(_timer_refs.values()):
                            try:
                                refs["row"].update()
                            except Exception:
                                pass
                except Exception:
                    pass
                await asyncio.sleep(1)

    return HomeRoot()
