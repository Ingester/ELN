"""
ELN App — Box Manager View
Lists all boxes, shows slot usage, allows create/edit/delete.
Clicking a box opens the grid detail view.
"""

from __future__ import annotations
import flet as ft
from typing import Callable, Optional

from components.box_grid import BoxGrid
from utils.i18n import tr


def _get(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def _open_overlay(page, ctrl):
    """Open a dialog/snackbar compatible with flet 0.70+."""
    if hasattr(page, "show_dialog"):
        try:
            ctrl.open = False
            page.show_dialog(ctrl)
            return
        except Exception:
            pass
    if ctrl not in page.overlay:
        page.overlay.append(ctrl)
    ctrl.open = True
    page.update()

def _close_overlay(page, ctrl):
    """Close a dialog/snackbar compatible with flet 0.70+."""
    ctrl.open = False
    page.update()




def build_box_manager_view(
    page: ft.Page,
    data_provider,
    is_mobile: bool = True,
) -> ft.Control:

    boxes_col = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=0)
    detail_container = ft.Container(expand=True, visible=False)
    _showing_detail: list[bool] = [False]
    _editing_box_id: list[Optional[int]] = [None]
    form_title = ft.Text(tr("新建 Box"), size=14, weight=ft.FontWeight.W_600)
    form_name = ft.TextField(label=tr("Box 名称"), dense=True, expand=True)
    form_size = ft.Dropdown(
        value="10",
        label=tr("尺寸"),
        options=[ft.dropdown.Option("9", "9×9"),
                 ft.dropdown.Option("10", "10×10")],
        width=130,
        dense=True,
    )
    form_panel = ft.Container(visible=False)
    status_text = ft.Text("", size=12, color=ft.Colors.GREY_600)
    _active_box_id: list[Optional[int]] = [None]
    _active_slot: list = [None]
    slot_title = ft.Text("", size=14, weight=ft.FontWeight.W_600)
    slot_sample = ft.TextField(label=tr("样品名称"), dense=True, expand=True)
    slot_notes = ft.TextField(label=tr("备注"), dense=True, multiline=True, min_lines=1, max_lines=3, expand=True)
    slot_status = ft.Text("", size=12, color=ft.Colors.GREY_600)
    slot_panel = ft.Container(visible=False)

    # ── Load box list ────────────────────────────
    def _load():
        boxes_col.controls.clear()
        try:
            boxes = data_provider.list_boxes()
        except Exception as e:
            boxes_col.controls.append(
                ft.Text(f"{tr('加载失败')}：{e}", color=ft.Colors.RED_400)
            )
            page.update()
            return

        if not boxes:
            boxes_col.controls.append(_empty_state())
        else:
            for b in boxes:
                boxes_col.controls.append(_build_box_card(b))
        page.update()

    def _build_box_card(b) -> ft.Container:
        bid = _get(b, "id")
        name = _get(b, "box_name", "Box")
        size = _get(b, "box_size", 10)
        if isinstance(b, dict):
            used = b.get("used_slots", 0)
            total = b.get("total_slots", size * size)
        else:
            try:
                used = data_provider.get_box_slot_count(bid)
            except Exception:
                used = 0
            total = size * size
        pct = used / total if total > 0 else 0

        return ft.Container(
            content=ft.Row([
                ft.Column([
                    ft.Text(name, size=15, weight=ft.FontWeight.BOLD),
                    ft.Text(f"{size}×{size} ({total} 格)  ·  {used}/{total} 已用",
                            size=12, color=ft.Colors.GREY_500),
                    ft.ProgressBar(
                        value=pct,
                        color=ft.Colors.ORANGE_600,
                        bgcolor=ft.Colors.GREY_200,
                        height=4,
                        width=160,
                    ),
                ], expand=True, spacing=4),
                ft.Row([
                    ft.IconButton(
                        ft.Icons.GRID_VIEW,
                        tooltip="查看网格",
                        on_click=lambda _, box_id=bid: _open_grid(box_id),
                        icon_color=ft.Colors.ORANGE_600,
                    ),
                    ft.IconButton(
                        ft.Icons.EDIT_OUTLINED,
                        tooltip="编辑",
                        on_click=lambda _, box_id=bid, box_name=name, box_size=size: _edit_box(box_id, box_name, box_size),
                        icon_color=ft.Colors.GREY_600,
                    ),
                    ft.IconButton(
                        ft.Icons.DELETE_OUTLINE,
                        tooltip="删除",
                        on_click=lambda _, box_id=bid, box_name=name: _confirm_delete(box_id, box_name),
                        icon_color=ft.Colors.RED_400,
                    ),
                ], spacing=0),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            border=ft.Border.all(1, ft.Colors.GREY_200),
            border_radius=8,
            padding=12,
            margin=ft.Margin.only(bottom=8),
            bgcolor=ft.Colors.WHITE,
            on_click=lambda _, box_id=bid: _open_grid(box_id),
            ink=True,
        )

    # ── Grid detail ──────────────────────────────
    def _open_grid(box_id: int):
        try:
            box_data = data_provider.get_box(box_id)
            slots_data = data_provider.get_slots(box_id)
        except Exception as e:
            _show_snack(f"加载失败：{e}", error=True)
            return

        from db.models import Box, BoxSlot
        if isinstance(box_data, dict):
            box = Box(id=box_data["id"], box_name=box_data["box_name"],
                      box_size=box_data.get("box_size", 10),
                      created_at=box_data.get("created_at", ""),
                      notes=box_data.get("notes", ""))
        else:
            box = box_data

        slots = []
        for s in slots_data:
            if isinstance(s, dict):
                slots.append(BoxSlot(
                    id=s["id"], box_id=s["box_id"],
                    row_label=s["row_label"], col_label=s["col_label"],
                    sample_name=s.get("sample_name", ""),
                    notes=s.get("notes", ""),
                    experiment_id=s.get("experiment_id"),
                    step_id=s.get("step_id"),
                    created_at=s.get("created_at", ""),
                ))
            else:
                slots.append(s)

        def _on_slot_click(slot: BoxSlot):
            _show_slot_detail(slot, box_id)

        grid = BoxGrid(box=box, slots=slots, mode="view",
                       on_slot_click=_on_slot_click)

        detail_container.content = ft.Column([
            ft.Row([
                ft.IconButton(ft.Icons.ARROW_BACK,
                              on_click=lambda _: _back_to_list(),
                              tooltip="返回列表"),
                ft.Text(box.box_name, size=16, weight=ft.FontWeight.BOLD),
            ]),
            slot_panel,
            ft.Container(content=grid, padding=8),
        ], scroll=ft.ScrollMode.AUTO)

        _showing_detail[0] = True
        _update_layout()

    def _back_to_list():
        _showing_detail[0] = False
        _load()
        _update_layout()

    def _show_slot_detail(slot, box_id: int):
        _active_box_id[0] = box_id
        _active_slot[0] = slot
        slot_title.value = f"槽位 {slot.position}"
        slot_sample.value = slot.sample_name
        slot_notes.value = slot.notes
        slot_status.value = ""
        slot_panel.visible = True
        page.update()

    def _save_slot_form(_):
        slot = _active_slot[0]
        box_id = _active_box_id[0]
        if slot is None or box_id is None:
            return
        try:
            try:
                data_provider.upsert_slot(
                    box_id=box_id,
                    position=slot.position,
                    sample_name=slot_sample.value or "",
                    notes=slot_notes.value or "",
                )
            except TypeError:
                data_provider.upsert_slot(
                    box_id=box_id,
                    row_label=slot.row_label,
                    col_label=slot.col_label,
                    sample_name=slot_sample.value or "",
                    notes=slot_notes.value or "",
                )
            slot_status.value = f"已保存 {slot.position}"
            slot_status.color = ft.Colors.GREEN_700
            _open_grid(box_id)
        except Exception as ex:
            slot_status.value = f"保存失败：{ex}"
            slot_status.color = ft.Colors.RED_400
            page.update()

    def _clear_slot_form(_):
        slot = _active_slot[0]
        box_id = _active_box_id[0]
        if slot is None or box_id is None:
            return
        try:
            try:
                data_provider.clear_slot(box_id, slot.position)
            except TypeError:
                data_provider.clear_slot(box_id, slot.row_label, slot.col_label)
            slot_status.value = f"已清空 {slot.position}"
            slot_status.color = ft.Colors.GREEN_700
            _open_grid(box_id)
        except Exception as ex:
            slot_status.value = f"清空失败：{ex}"
            slot_status.color = ft.Colors.RED_400
            page.update()

    def _cancel_slot_form(_=None):
        _active_slot[0] = None
        slot_panel.visible = False
        page.update()

    # ── Create / Edit box ────────────────────────
    def _new_box_form(size: int = 10):
        _editing_box_id[0] = None
        form_title.value = tr("新建 Box")
        form_name.value = _next_box_name(size)
        form_size.value = str(size)
        form_name.error_text = None
        form_panel.visible = True
        page.update()

    def _next_box_name(size: int) -> str:
        try:
            boxes = data_provider.list_boxes()
        except Exception:
            boxes = []
        prefix = f"Box {size}×{size}"
        names = {_get(b, "box_name", "") for b in boxes}
        index = 1
        while f"{prefix} #{index}" in names:
            index += 1
        return f"{prefix} #{index}"

    def _show_new_box_choices(_=None):
        _new_box_form(10)

    def _edit_box(box_id: int, name: str, size: int):
        _show_box_form(box_id, name, size)

    def _show_box_form(box_id: Optional[int], name: str, size: int):
        _editing_box_id[0] = box_id
        form_title.value = tr("新建 Box") if box_id is None else tr("编辑 Box")
        form_name.value = name
        form_name.error_text = None
        form_size.value = str(size or 10)
        form_panel.visible = True
        page.update()

    def _save_box_form(_):
        n = (form_name.value or "").strip()
        if not n:
            form_name.error_text = "请输入名称"
            page.update()
            return
        s = int(form_size.value or "10")
        try:
            if _editing_box_id[0] is None:
                data_provider.create_box(n, s)
                status_text.value = f"已新建 {n}，点击卡片进入 {s * s} 格视图"
            else:
                data_provider.update_box(_editing_box_id[0], box_name=n, box_size=s)
                status_text.value = f"已更新 {n}"
            status_text.color = ft.Colors.GREEN_700
            _cancel_box_form()
            _load()
        except Exception as ex:
            _show_snack(f"保存失败：{ex}", error=True)

    def _cancel_box_form(_=None):
        _editing_box_id[0] = None
        form_name.value = ""
        form_name.error_text = None
        form_size.value = "10"
        form_panel.visible = False
        page.update()

    def _confirm_delete(box_id: int, name: str):
        def _do(_):
            _close_overlay(page, dlg)
            try:
                data_provider.delete_box(box_id)
                _load()
            except Exception as ex:
                _show_snack(f"删除失败：{ex}", error=True)

        dlg = ft.AlertDialog(
            title=ft.Text("确认删除"),
            content=ft.Text(f"删除 Box「{name}」及其所有槽位数据？"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: _close_dlg(dlg)),
                ft.ElevatedButton("删除", on_click=_do,
                                   bgcolor=ft.Colors.RED_600,
                                   color=ft.Colors.WHITE),
            ],
        )
        _open_overlay(page, dlg)

    def _empty_state() -> ft.Container:
        return ft.Container(
            content=ft.Column([
                ft.Icon(ft.Icons.INVENTORY_2_OUTLINED, size=64,
                        color=ft.Colors.GREY_300),
                ft.Text(tr("暂无 Box"), size=16, color=ft.Colors.GREY_500,
                        text_align=ft.TextAlign.CENTER),
                ft.ElevatedButton("+ " + tr("新建 Box") + " 10×10", on_click=lambda _: _new_box_form(10),
                                   bgcolor=ft.Colors.ORANGE_600,
                                   color=ft.Colors.WHITE),
                ft.OutlinedButton("+ " + tr("新建 Box") + " 9×9", on_click=lambda _: _new_box_form(9)),
                ft.Text(tr("点击 Box 卡片进入格子视图"),
                        size=12, color=ft.Colors.GREY_500),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12),
            alignment=ft.Alignment.CENTER, padding=40,
        )

    def _box_create_buttons() -> ft.Row:
        return ft.Row([
            ft.ElevatedButton("+ 新建 10×10", on_click=lambda _: _new_box_form(10),
                              bgcolor=ft.Colors.ORANGE_600, color=ft.Colors.WHITE,
                              height=32),
            ft.OutlinedButton("+ 新建 9×9", on_click=lambda _: _new_box_form(9),
                              height=32),
        ], spacing=8)

    def _close_dlg(dlg):
        _close_overlay(page, dlg)

    def _show_snack(msg: str, error: bool = False):
        _open_overlay(page, ft.SnackBar(            content=ft.Text(msg),            bgcolor=ft.Colors.RED_400 if error else None,        ))

    def _update_layout():
        if _showing_detail[0]:
            list_panel.visible = False
            detail_container.visible = True
        else:
            list_panel.visible = True
            detail_container.visible = False
        try:
            page.update()
        except Exception:
            pass

    # ── Header ──────────────────────────────────
    header = ft.Row([
        ft.Text(tr("Box 管理"), size=20, weight=ft.FontWeight.BOLD),
        ft.Container(expand=True),
        _box_create_buttons(),
        ft.IconButton(ft.Icons.REFRESH, on_click=lambda _: _load(),
                      icon_color=ft.Colors.GREY_600),
    ])

    form_panel.content = ft.Container(
        content=ft.Column([
            form_title,
            ft.Row([
                form_name,
                form_size,
                ft.ElevatedButton(
                    "保存",
                    on_click=_save_box_form,
                    bgcolor=ft.Colors.ORANGE_600,
                    color=ft.Colors.WHITE,
                    height=40,
                ),
                ft.TextButton("取消", on_click=_cancel_box_form),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=8),
        bgcolor=ft.Colors.WHITE,
        border=ft.Border.all(1, ft.Colors.ORANGE_200),
        border_radius=8,
        padding=12,
        margin=ft.Margin.symmetric(horizontal=12, vertical=8),
    )

    slot_panel.content = ft.Container(
        content=ft.Column([
            slot_title,
            ft.Row([slot_sample, slot_notes], spacing=12),
            ft.Row([
                ft.ElevatedButton("保存槽位", on_click=_save_slot_form,
                                  bgcolor=ft.Colors.ORANGE_600,
                                  color=ft.Colors.WHITE,
                                  height=36),
                ft.TextButton("清空", on_click=_clear_slot_form,
                              style=ft.ButtonStyle(color=ft.Colors.RED_400)),
                ft.TextButton("取消", on_click=_cancel_slot_form),
                slot_status,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=8),
        bgcolor=ft.Colors.WHITE,
        border=ft.Border.all(1, ft.Colors.ORANGE_200),
        border_radius=8,
        padding=12,
        margin=ft.Margin.only(bottom=8),
    )

    list_panel = ft.Column([
        ft.Container(content=header,
                     padding=ft.Padding.symmetric(horizontal=16, vertical=8)),
        ft.Divider(height=1, color=ft.Colors.GREY_200),
        ft.Container(content=status_text,
                     padding=ft.Padding.symmetric(horizontal=16, vertical=4)),
        form_panel,
        ft.Container(content=boxes_col,
                     padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                     expand=True),
    ], expand=True, spacing=0)

    _load()

    return ft.Stack([
        list_panel,
        detail_container,
    ], expand=True)
