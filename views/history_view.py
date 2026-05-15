"""
ELN App — History View
Full-screen page (not a Tab) showing completed/archived experiments.
Accessible from Home top-right icon.
"""

from __future__ import annotations
import flet as ft
from typing import Callable


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
    on_open_report: Callable[[int], None],
    on_reuse_protocol: Callable[[dict], None],
    is_mobile: bool = True,
) -> ft.Control:

    list_col = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=0)

    def _load():
        list_col.controls.clear()
        try:
            completed = data_provider.list_experiments(status="completed")
            archived  = data_provider.list_experiments(status="archived")
            all_exps  = completed + archived
        except Exception as e:
            list_col.controls.append(
                ft.Text(f"加载失败：{e}", color=ft.Colors.RED_400)
            )
            page.update()
            return

        if not all_exps:
            list_col.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Icon(ft.Icons.HISTORY, size=64, color=ft.Colors.GREY_300),
                        ft.Text("暂无历史记录", size=16, color=ft.Colors.GREY_500,
                                text_align=ft.TextAlign.CENTER),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12),
                    alignment=ft.Alignment.CENTER, padding=40,
                )
            )
        else:
            for exp in all_exps:
                list_col.controls.append(_build_exp_card(exp))
        page.update()

    def _build_exp_card(exp) -> ft.Container:
        exp_id = _get(exp, "id")
        name = _get(exp, "name", "实验")
        status = _get(exp, "status", "completed")
        created = (_get(exp, "created_at", "") or "")[:10]
        progress = {}
        if not isinstance(exp, dict):
            try:
                progress = data_provider.get_experiment_progress(exp_id)
            except Exception:
                progress = {}
        total = _get(exp, "total_steps", progress.get("total_steps", 0))
        completed_steps = _get(exp, "completed_steps", progress.get("completed_steps", 0))

        status_color = ft.Colors.GREEN_600 if status == "completed" else ft.Colors.GREY_500
        status_label = "已完成" if status == "completed" else "已归档"

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
                        ft.Text(f"{created}  ·  {completed_steps}/{total} 步",
                                size=12, color=ft.Colors.GREY_500),
                    ], spacing=6),
                ], expand=True, spacing=4),
                ft.Row([
                    ft.IconButton(
                        ft.Icons.SUMMARIZE_OUTLINED,
                        tooltip="查看报告",
                        on_click=lambda _, eid=exp_id: on_open_report(eid),
                        icon_color=ft.Colors.ORANGE_600,
                    ),
                    ft.IconButton(
                        ft.Icons.REPLAY,
                        tooltip="重新使用协议",
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

    def _reuse_protocol(exp):
        """Create a new experiment from the same protocol."""
        tf = ft.TextField(
            value=_get(exp, "name", "") + " (重复)",
            label="新实验名称",
            autofocus=True,
        )

        def _create(_):
            name = tf.value.strip()
            if not name:
                tf.error_text = "请输入名称"
                page.update()
                return
            _close_overlay(page, dlg)
            on_reuse_protocol({
                "name": name,
                "protocol_json": _get(exp, "protocol_json", "{}"),
                "protocol_id": _get(exp, "protocol_id"),
            })

        dlg = ft.AlertDialog(
            title=ft.Text("重新使用协议"),
            content=tf,
            actions=[
                ft.TextButton("取消", on_click=lambda _: _close_dlg(dlg)),
                ft.ElevatedButton("开始", on_click=_create,
                                   bgcolor=ft.Colors.ORANGE_600,
                                   color=ft.Colors.WHITE),
            ],
        )
        _open_overlay(page, dlg)

    def _close_dlg(dlg):
        _close_overlay(page, dlg)

    header = ft.Row([
        ft.IconButton(ft.Icons.ARROW_BACK,
                      on_click=lambda _: on_back(),
                      tooltip="返回"),
        ft.Text("历史记录", size=18, weight=ft.FontWeight.BOLD),
        ft.Container(expand=True),
        ft.IconButton(ft.Icons.REFRESH,
                      on_click=lambda _: _load(),
                      icon_color=ft.Colors.GREY_600),
    ])

    _load()

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
