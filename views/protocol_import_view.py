"""
ELN App — Protocol Import View
Paste Protocol JSON → validate → preview → save to library or start experiment.
"""

from __future__ import annotations
import json
import flet as ft
from typing import Callable, Optional

from utils.protocol_parser import validate_protocol
from db.models import ProtocolDefinition

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




def build_protocol_import_view(
    page: ft.Page,
    data_provider,
    on_save_to_library: Callable[[int], None],       # called with saved protocol id
    on_start_experiment: Callable[[dict], None],     # called with {name, protocol_json}
    on_cancel: Callable[[], None],
    is_mobile: bool = True,
) -> ft.Control:

    _parsed_def: list[Optional[ProtocolDefinition]] = [None]

    # ── Input area ──────────────────────────────
    tf_input = ft.TextField(
        label="粘贴协议 JSON",
        multiline=True,
        min_lines=8,
        max_lines=16,
        hint_text='{\n  "protocol_name": "Colony PCR",\n  "steps": [...]\n}',
        border_color=ft.Colors.ORANGE_300,
        focused_border_color=ft.Colors.ORANGE_600,
    )

    parse_status = ft.Text("", size=13)
    warnings_col = ft.Column(spacing=4, visible=False)
    preview_col  = ft.Column(spacing=6, visible=False)

    # ── Parse button ────────────────────────────
    def _on_parse(_):
        text = tf_input.value.strip()
        if not text:
            parse_status.value = "请先粘贴协议内容"
            parse_status.color = ft.Colors.RED_400
            page.update()
            return

        parse_status.value = "解析中…"
        parse_status.color = ft.Colors.GREY_500
        page.update()

        try:
            pdef = ProtocolDefinition.from_json(text)
            _parsed_def[0] = pdef
            warnings = validate_protocol(pdef)

            parse_status.value = f"✅ 解析成功：{pdef.protocol_name}（{len(pdef.steps)} 步）"
            parse_status.color = ft.Colors.GREEN_600

            # Show warnings
            warnings_col.controls.clear()
            if warnings:
                warnings_col.visible = True
                warnings_col.controls.append(
                    ft.Text("⚠️ 注意事项：", size=12, color=ft.Colors.ORANGE_700)
                )
                for w in warnings:
                    warnings_col.controls.append(
                        ft.Text(f"  • {w}", size=12, color=ft.Colors.ORANGE_600)
                    )
            else:
                warnings_col.visible = False

            # Build preview
            _build_preview(pdef)
            preview_col.visible = True
            action_row.visible = True

        except Exception as ex:
            parse_status.value = f"❌ 解析失败：{ex}"
            parse_status.color = ft.Colors.RED_400
            preview_col.visible = False
            action_row.visible = False

        page.update()

    def _build_preview(pdef: ProtocolDefinition):
        preview_col.controls.clear()
        preview_col.controls.append(
            ft.Container(
                content=ft.Column([
                    ft.Text("预览", size=14, weight=ft.FontWeight.BOLD),
                    ft.Text(f"协议名称：{pdef.protocol_name}", size=13),
                    ft.Text(f"版本：{pdef.version}  作者：{pdef.author or '—'}", size=12,
                            color=ft.Colors.GREY_600),
                    ft.Divider(height=1),
                    ft.Text(f"共 {len(pdef.steps)} 个步骤：", size=13),
                    *[
                        ft.Row([
                            ft.Text(f"  {i+1}. {s.title}", size=12, expand=True),
                            ft.Text(
                                f"⏱{s.timer_seconds//60}min" if s.timer_seconds else "",
                                size=11, color=ft.Colors.ORANGE_500,
                            ),
                            ft.Text("📷" if s.has_camera else "", size=11),
                        ])
                        for i, s in enumerate(pdef.steps)
                    ],
                ], spacing=4),
                border=ft.Border.all(1, ft.Colors.GREY_200),
                border_radius=8,
                padding=12,
                bgcolor=ft.Colors.GREY_50,
            )
        )

    # ── Action buttons ───────────────────────────
    def _save_to_library(_):
        pdef = _parsed_def[0]
        if pdef is None:
            return
        try:
            result = data_provider.create_protocol(pdef.to_json())
            pid = result["id"] if isinstance(result, dict) else result.id
            _show_snack("已保存到协议库")
            on_save_to_library(pid)
        except Exception as ex:
            _show_snack(f"保存失败：{ex}", error=True)

    def _start_now(_):
        pdef = _parsed_def[0]
        if pdef is None:
            return
        tf_name = ft.TextField(
            value=pdef.protocol_name,
            label="实验名称",
            autofocus=True,
        )

        def _create(_):
            name = tf_name.value.strip()
            if not name:
                tf_name.error_text = "请输入实验名称"
                page.update()
                return
            _close_overlay(page, dlg)
            on_start_experiment({
                "name": name,
                "protocol_json": pdef.to_json(),
                "protocol_id": None,
            })

        dlg = ft.AlertDialog(
            title=ft.Text("直接开始实验"),
            content=tf_name,
            actions=[
                ft.TextButton("取消", on_click=lambda _: _close_dlg(dlg)),
                ft.ElevatedButton("开始", on_click=_create,
                                   bgcolor=ft.Colors.ORANGE_600,
                                   color=ft.Colors.WHITE),
            ],
        )
        _open_overlay(page, dlg)

    action_row = ft.Row([
        ft.ElevatedButton(
            "保存到协议库",
            on_click=_save_to_library,
            bgcolor=ft.Colors.ORANGE_600,
            color=ft.Colors.WHITE,
        ),
        ft.OutlinedButton(
            "直接开始实验",
            on_click=_start_now,
            style=ft.ButtonStyle(color=ft.Colors.ORANGE_600),
        ),
    ], spacing=12, visible=False)

    def _close_dlg(dlg):
        _close_overlay(page, dlg)

    def _show_snack(msg: str, error: bool = False):
        _open_overlay(page, ft.SnackBar(            content=ft.Text(msg),            bgcolor=ft.Colors.RED_400 if error else ft.Colors.GREEN_600,        ))

    # ── Header ──────────────────────────────────
    header = ft.Row([
        ft.IconButton(ft.Icons.ARROW_BACK,
                      on_click=lambda _: on_cancel(),
                      tooltip="返回"),
        ft.Text("导入协议", size=18, weight=ft.FontWeight.BOLD),
    ])

    return ft.Column([
        ft.Container(content=header,
                     padding=ft.Padding.symmetric(horizontal=8, vertical=8),
                     border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.GREY_200))),
        ft.Container(
            content=ft.Column([
                tf_input,
                ft.ElevatedButton(
                    "解析",
                    on_click=_on_parse,
                    bgcolor=ft.Colors.ORANGE_600,
                    color=ft.Colors.WHITE,
                ),
                parse_status,
                warnings_col,
                preview_col,
                action_row,
            ], scroll=ft.ScrollMode.AUTO, spacing=12),
            padding=16,
            expand=True,
        ),
    ], expand=True, spacing=0)
