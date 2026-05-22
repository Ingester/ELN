"""
ELN App — Protocol Library View
Lists all saved protocol templates.
Actions: New Experiment, Edit, Duplicate, Delete, Import.
"""

from __future__ import annotations
import flet as ft
from typing import Callable, Optional
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




def build_protocol_library_view(
    page: ft.Page,
    data_provider,
    on_new_experiment: Callable[[dict], None],   # called with protocol dict
    on_edit_protocol: Callable[[int], None],      # navigate to editor
    on_import_protocol: Callable[[], None],       # navigate to import view
    is_mobile: bool = True,
) -> ft.Control:

    protocols_list = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=0)

    def _load():
        protocols_list.controls.clear()
        try:
            protocols = data_provider.list_protocols()
        except Exception as e:
            protocols_list.controls.append(
                ft.Text(f"{tr('加载失败')}：{e}", color=ft.Colors.RED_400)
            )
            page.update()
            return

        if not protocols:
            protocols_list.controls.append(_empty_state())
        else:
            for p in protocols:
                protocols_list.controls.append(_build_protocol_card(p))
        page.update()

    def _build_protocol_card(p) -> ft.Container:
        pid = _get(p, "id")
        name = _get(p, "name", "协议")
        version = _get(p, "version", "1.0")
        author = _get(p, "author", "")
        use_count = _get(p, "use_count", 0)
        last_used = _get(p, "last_used_at", "")

        # Parse step count from protocol_json
        import json
        try:
            pdef = json.loads(_get(p, "protocol_json", "{}"))
            step_count = len(pdef.get("steps", []))
        except Exception:
            step_count = 0

        subtitle_parts = [f"v{version}"]
        if author:
            subtitle_parts.append(author)
        subtitle_parts.append(f"{step_count} {tr('步')}")
        if use_count:
            subtitle_parts.append(f"{tr('已用')} {use_count} {tr('次')}")

        def _start_experiment(_):
            _show_name_dialog(p)

        def _edit(_):
            on_edit_protocol(pid)

        def _duplicate(_):
            try:
                import json as _json
                pdef = _json.loads(_get(p, "protocol_json", "{}"))
                pdef["protocol_name"] = pdef.get("protocol_name", name) + " (副本)"
                import json as j
                data_provider.create_protocol(j.dumps(pdef, ensure_ascii=False))
                _load()
            except Exception as ex:
                _show_snack(f"复制失败：{ex}", error=True)

        def _delete(_):
            _confirm_delete(pid, name)

        menu_items = [
            ft.PopupMenuItem(content=tr("编辑"), on_click=_edit),
            ft.PopupMenuItem(content=tr("复制"), on_click=_duplicate),
            ft.PopupMenuItem(content=tr("删除"), on_click=_delete),
        ]

        return ft.Container(
            content=ft.Row([
                ft.Column([
                    ft.Text(name, size=15, weight=ft.FontWeight.BOLD),
                    ft.Text(" · ".join(subtitle_parts), size=12,
                            color=ft.Colors.GREY_500),
                ], expand=True, spacing=2),
                ft.ElevatedButton(
                    tr("新建实验"),
                    on_click=_start_experiment,
                    bgcolor=ft.Colors.ORANGE_600,
                    color=ft.Colors.WHITE,
                    height=32,
                ),
                ft.PopupMenuButton(items=menu_items),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            border=ft.Border.all(1, ft.Colors.GREY_200),
            border_radius=8,
            padding=12,
            margin=ft.Margin.only(bottom=8),
            bgcolor=ft.Colors.WHITE,
        )

    def _show_name_dialog(p: dict) -> None:
        """Ask user for experiment name before creating."""
        import json
        pdef = json.loads(_get(p, "protocol_json", "{}"))
        default_name = pdef.get("protocol_name", _get(p, "name", "实验"))

        tf = ft.TextField(
            value=default_name,
            label=tr("实验名称"),
            autofocus=True,
        )

        def _create(_):
            name = tf.value.strip()
            if not name:
                tf.error_text = tr("请输入实验名称")
                page.update()
                return
            _close_overlay(page, dlg)
            on_new_experiment({
                "name": name,
                "protocol_json": _get(p, "protocol_json", "{}"),
                "protocol_id": _get(p, "id"),
            })

        dlg = ft.AlertDialog(
            title=ft.Text(tr("新建实验")),
            content=tf,
            actions=[
                ft.TextButton(tr("取消"), on_click=lambda _: _close_dlg(dlg)),
                ft.ElevatedButton(tr("开始"), on_click=_create,
                                   bgcolor=ft.Colors.ORANGE_600,
                                   color=ft.Colors.WHITE),
            ],
        )
        _open_overlay(page, dlg)

    def _confirm_delete(pid: int, name: str) -> None:
        def _do_delete(_):
            _close_overlay(page, dlg)
            try:
                data_provider.delete_protocol(pid)
                _load()
            except Exception as ex:
                _show_snack(f"删除失败：{ex}", error=True)

        dlg = ft.AlertDialog(
            title=ft.Text(tr("确认删除")),
            content=ft.Text(f"删除协议「{name}」？此操作不可撤销。"),
            actions=[
                ft.TextButton(tr("取消"), on_click=lambda _: _close_dlg(dlg)),
                ft.ElevatedButton(tr("删除"), on_click=_do_delete,
                                   bgcolor=ft.Colors.RED_600,
                                   color=ft.Colors.WHITE),
            ],
        )
        _open_overlay(page, dlg)

    def _empty_state() -> ft.Container:
        return ft.Container(
            content=ft.Column([
                ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=64,
                        color=ft.Colors.GREY_300),
                ft.Text(tr("暂无协议"), size=16, color=ft.Colors.GREY_500,
                        text_align=ft.TextAlign.CENTER),
                ft.Text(tr("导入或新建一个协议模板开始使用"),
                        size=13, color=ft.Colors.GREY_400,
                        text_align=ft.TextAlign.CENTER),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12),
            alignment=ft.Alignment.CENTER,
            padding=40,
        )

    def _close_dlg(dlg) -> None:
        _close_overlay(page, dlg)

    def _show_snack(msg: str, error: bool = False) -> None:
        _open_overlay(page, ft.SnackBar(            content=ft.Text(msg),            bgcolor=ft.Colors.RED_400 if error else None,        ))

    # ── Header ──────────────────────────────────
    header = ft.Row([
        ft.Text(tr("协议库"), size=20, weight=ft.FontWeight.BOLD),
        ft.Container(expand=True),
        ft.IconButton(
            ft.Icons.UPLOAD_FILE,
            tooltip=tr("导入协议"),
            on_click=lambda _: on_import_protocol(),
            icon_color=ft.Colors.ORANGE_600,
        ),
        ft.IconButton(
            ft.Icons.REFRESH,
            tooltip=tr("刷新"),
            on_click=lambda _: _load(),
            icon_color=ft.Colors.GREY_600,
        ),
    ])

    _load()

    return ft.Column([
        ft.Container(content=header,
                     padding=ft.Padding.symmetric(horizontal=16, vertical=8)),
        ft.Divider(height=1, color=ft.Colors.GREY_200),
        ft.Container(
            content=protocols_list,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            expand=True,
        ),
    ], expand=True, spacing=0)
