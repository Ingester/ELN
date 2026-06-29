"""
ELN App — History View
Full-screen page (not a Tab) showing completed/archived experiments.
Accessible from Home top-right icon.
"""

from __future__ import annotations
from datetime import datetime
import flet as ft
from typing import Callable
from utils.i18n import tr


def _get(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

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




def build_history_view(
    page: ft.Page,
    data_provider,
    on_back: Callable[[], None],
    report_url: Callable[[int], str],
    on_reuse_protocol: Callable[[dict], None],
    on_continue_experiment: Callable[[int], None],
    is_mobile: bool = True,
) -> ft.Control:

    list_col = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=0)
    page_size = 20
    loaded_count = [0]
    has_more = [False]
    loading_text = ft.Text("", size=12, color=ft.Colors.GREY_500)

    def _load(reset: bool = True):
        if reset:
            loaded_count[0] = 0
            list_col.controls.clear()
        try:
            if hasattr(data_provider, "list_experiment_summaries"):
                all_exps = data_provider.list_experiment_summaries(
                    status="completed,archived",
                    limit=page_size + 1,
                    offset=loaded_count[0],
                )
            else:
                completed = data_provider.list_experiments(status="completed")
                archived = data_provider.list_experiments(status="archived")
                all_exps = (completed + archived)[loaded_count[0]:loaded_count[0] + page_size + 1]
        except Exception as e:
            if reset:
                list_col.controls.clear()
            list_col.controls.append(
                ft.Text(f"{tr('加载失败')}：{e}", color=ft.Colors.RED_400)
            )
            page.update()
            return

        visible_exps = all_exps[:page_size]
        has_more[0] = len(all_exps) > page_size

        if reset and not visible_exps:
            list_col.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Icon(ft.Icons.HISTORY, size=64, color=ft.Colors.GREY_300),
                        ft.Text(tr("暂无历史记录"), size=16, color=ft.Colors.GREY_500,
                                text_align=ft.TextAlign.CENTER),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12),
                    alignment=ft.Alignment.CENTER, padding=40,
                )
            )
        else:
            if not reset and list_col.controls and getattr(list_col.controls[-1], "data", None) == "load_more":
                list_col.controls.pop()
            for exp in visible_exps:
                list_col.controls.append(_build_exp_card(exp))
            loaded_count[0] += len(visible_exps)
            if has_more[0]:
                list_col.controls.append(_load_more_control())
        loading_text.value = f"{tr('已加载')} {loaded_count[0]} {tr('条')}" if loaded_count[0] else ""
        page.update()

    def _load_more_control() -> ft.Container:
        return ft.Container(
            data="load_more",
            content=ft.TextButton(
                tr("加载更多"),
                on_click=lambda _: _load(reset=False),
                style=ft.ButtonStyle(color=ft.Colors.ORANGE_600),
            ),
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.symmetric(vertical=8),
        )

    def _build_exp_card(exp) -> ft.Container:
        exp_id = _get(exp, "id")
        name = _get(exp, "name", "实验")
        status = _get(exp, "status", "completed")
        created = _fmt_dt(_get(exp, "created_at", ""))
        progress = {}
        if not isinstance(exp, dict):
            try:
                progress = data_provider.get_experiment_progress(exp_id)
            except Exception:
                progress = {}
        total = _get(exp, "total_steps", progress.get("total_steps", 0))
        completed_steps = _get(exp, "completed_steps", progress.get("completed_steps", 0))
        completed_at = _get(exp, "completed_at", progress.get("completed_at", ""))

        status_color = ft.Colors.GREEN_600 if status == "completed" else ft.Colors.GREY_500
        status_label = tr("已完成") if status == "completed" else tr("已放弃")
        time_parts = [f"{tr('创建：')}{created or '—'}"]
        if completed_at:
            time_parts.append(f"{tr('结束：')}{_fmt_dt(completed_at)}")
        time_parts.append(f"{completed_steps}/{total} {tr('步')}")

        return ft.Container(
            content=ft.Row([
                ft.Column([
                    ft.Text(name, size=14, weight=ft.FontWeight.BOLD),
                    ft.Row([
                        ft.Container(
                            content=ft.Text(status_label, size=11,
                                            color=ft.Colors.WHITE),
                            bgcolor=status_color,
                            border_radius=8,
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                        ),
                        ft.Text("  ·  ".join(time_parts),
                                size=12, color=ft.Colors.GREY_500),
                    ], spacing=6),
                ], expand=True, spacing=4),
                ft.Row([
                    ft.IconButton(
                        ft.Icons.PLAY_ARROW,
                        tooltip=tr("继续实验"),
                        visible=status == "archived",
                        on_click=lambda _, eid=exp_id: _continue_experiment(eid),
                        icon_color=ft.Colors.GREEN_600,
                    ),
                    ft.IconButton(
                        ft.Icons.SUMMARIZE_OUTLINED,
                        tooltip=tr("查看报告"),
                        url=ft.Url(
                            report_url(exp_id),
                            target=ft.UrlTarget.SELF,
                        ),
                        icon_color=ft.Colors.ORANGE_600,
                    ),
                    ft.IconButton(
                        ft.Icons.REPLAY,
                        tooltip=tr("重新使用协议"),
                        on_click=lambda _, e=exp: _reuse_protocol(e),
                        icon_color=ft.Colors.GREY_600,
                    ),
                ], spacing=0),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            border=ft.Border.all(1, ft.Colors.GREY_200),
            border_radius=8,
            padding=12,
            margin=ft.Margin.only(bottom=8),
            bgcolor=ft.Colors.WHITE,
        )

    def _continue_experiment(exp_id: int) -> None:
        try:
            data_provider.update_experiment(exp_id, status="active")
            on_continue_experiment(exp_id)
        except Exception as ex:
            _open_overlay(
                page,
                ft.SnackBar(
                    content=ft.Text(f"{tr('继续失败')}：{ex}"),
                    bgcolor=ft.Colors.RED_400,
                ),
            )

    def _reuse_protocol(exp):
        """Create a new experiment from the same protocol."""
        if not _get(exp, "protocol_json", ""):
            try:
                exp = data_provider.get_experiment(_get(exp, "id"))
            except Exception as ex:
                _open_overlay(
                    page,
                    ft.SnackBar(
                        content=ft.Text(f"{tr('加载失败')}：{ex}"),
                        bgcolor=ft.Colors.RED_400,
                    ),
                )
                return
        tf = ft.TextField(
            value=_get(exp, "name", "") + " (重复)",
            label=tr("新实验名称"),
            autofocus=True,
        )

        def _create(_):
            name = tf.value.strip()
            if not name:
                tf.error_text = tr("请输入名称")
                page.update()
                return
            _close_overlay(page, dlg)
            on_reuse_protocol({
                "name": name,
                "protocol_json": _get(exp, "protocol_json", "{}"),
                "protocol_id": _get(exp, "protocol_id"),
            })

        dlg = ft.AlertDialog(
            title=ft.Text(tr("重新使用协议")),
            content=tf,
            actions=[
                ft.TextButton(tr("取消"), on_click=lambda _: _close_dlg(dlg)),
                ft.ElevatedButton(tr("开始"), on_click=_create,
                                   bgcolor=ft.Colors.ORANGE_600,
                                   color=ft.Colors.WHITE),
            ],
        )
        _open_overlay(page, dlg)

    def _close_dlg(dlg):
        _close_overlay(page, dlg)

    def _fmt_dt(value: str) -> str:
        if not value:
            return ""
        try:
            dt = datetime.fromisoformat(str(value))
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(value)[:16]

    header = ft.Row([
        ft.IconButton(ft.Icons.ARROW_BACK,
                      on_click=lambda _: on_back(),
                      tooltip=tr("返回")),
        ft.Text(tr("历史记录"), size=18, weight=ft.FontWeight.BOLD),
        loading_text,
        ft.Container(expand=True),
        ft.IconButton(ft.Icons.REFRESH,
                      on_click=lambda _: _load(reset=True),
                      icon_color=ft.Colors.GREY_600),
    ])

    _load(reset=True)

    return ft.Column([
        ft.Container(
            content=header,
            padding=ft.Padding.symmetric(horizontal=8, vertical=8),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.GREY_200)),
        ),
        ft.Container(
            content=list_col,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            expand=True,
        ),
    ], expand=True, spacing=0)
