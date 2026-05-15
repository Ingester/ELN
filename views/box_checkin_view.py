"""
ELN App — Box Checkin View
Registers storage items at experiment end.
Mobile: per-item modal dialog (select box → click grid slot).
Desktop: dual-pane (item list left, box grid right).
Switchable between modes.
"""

from __future__ import annotations
import flet as ft
from typing import Callable, Optional

from db.models import StorageItem, Box, BoxSlot
from components.box_grid import BoxGrid

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




def build_box_checkin_view(
    page: ft.Page,
    data_provider,
    experiment_id: int,
    on_done: Callable[[], None],          # navigate to report after checkin
    on_skip: Callable[[], None],          # skip checkin, go to report
    is_mobile: bool = True,
) -> ft.Control:

    # ── Load data ────────────────────────────────
    try:
        items_data = data_provider.get_storage_items(experiment_id)
        boxes_data = data_provider.list_boxes()
    except Exception as e:
        return ft.Container(
            content=ft.Text(f"加载失败：{e}", color=ft.Colors.RED_400),
            padding=20,
        )

    def _to_storage_item(d) -> StorageItem:
        if isinstance(d, StorageItem):
            return d
        return StorageItem(
            id=d["id"], experiment_id=d["experiment_id"],
            item_key=d["item_key"], item_label=d["item_label"],
            tube_type=d.get("tube_type", ""),
            notes_template=d.get("notes_template", ""),
            default_box=d.get("default_box", ""),
            box_id=d.get("box_id"), row_label=d.get("row_label"),
            col_label=d.get("col_label"), notes=d.get("notes", ""),
            registered_at=d.get("registered_at"),
        )

    def _to_box(d) -> Box:
        if isinstance(d, Box):
            return d
        return Box(id=d["id"], box_name=d["box_name"],
                   box_size=d.get("box_size", 10),
                   created_at=d.get("created_at", ""),
                   notes=d.get("notes", ""))

    items: list[StorageItem] = [_to_storage_item(d) for d in items_data]
    boxes: list[Box] = [_to_box(d) for d in boxes_data]
    boxes_by_id: dict[int, Box] = {b.id: b for b in boxes}

    if not items:
        return ft.Container(
            content=ft.Column([
                ft.Text("此实验无需存储登记", size=15, color=ft.Colors.GREY_500),
                ft.Text(
                    "原因：创建实验时使用的 protocol 没有定义 storage_items。",
                    size=12, color=ft.Colors.GREY_500,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.ElevatedButton("查看报告", on_click=lambda _: on_done(),
                                   bgcolor=ft.Colors.ORANGE_600, color=ft.Colors.WHITE),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=16),
            alignment=ft.Alignment.CENTER, padding=40,
        )

    if not boxes:
        return ft.Container(
            content=ft.Column([
                ft.Text("暂无 Box", size=15, color=ft.Colors.GREY_500),
                ft.Text(
                    "请先到左侧 Box 页面新建 9x9 或 10x10 Box，然后回到这里登记样品位置。",
                    size=12, color=ft.Colors.GREY_500,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.TextButton("跳过，查看报告", on_click=lambda _: on_skip()),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=16),
            alignment=ft.Alignment.CENTER, padding=40,
        )

    # ── State ────────────────────────────────────
    _current_item_idx: list[int] = [0]
    _selected_box_id: list[Optional[int]] = [None]
    _selected_pos: list[Optional[tuple]] = [None]   # (row_label, col_label)

    # ── Mobile: per-item modal flow ──────────────
    def _open_item_modal(item: StorageItem):
        _selected_box_id[0] = item.box_id
        _selected_pos[0] = (item.row_label, item.col_label) if item.row_label else None

        # Box selector dropdown
        dd_box = ft.Dropdown(
            label="选择 Box",
            value=str(item.box_id) if item.box_id else None,
            options=[ft.dropdown.Option(str(b.id), b.box_name) for b in boxes],
            on_select=lambda e: _on_box_selected(e.control.value, grid_container),
        )

        grid_container = ft.Container()
        notes_tf = ft.TextField(
            value=item.notes or item.notes_template,
            label="备注",
            multiline=True, min_lines=2,
        )

        if item.box_id:
            _render_grid(item.box_id, grid_container,
                         item.row_label, item.col_label)

        def _on_box_selected(box_id_str: str, container: ft.Container):
            if box_id_str:
                bid = int(box_id_str)
                _selected_box_id[0] = bid
                _selected_pos[0] = None
                _render_grid(bid, container, None, None)
                page.update()

        def _render_grid(box_id: int, container: ft.Container,
                          sel_row: Optional[str], sel_col: Optional[str]):
            try:
                slots_data = data_provider.get_slots(box_id)
                box = boxes_by_id.get(box_id)
                if box is None:
                    return
                slots = [_to_slot(s) for s in slots_data]
                sel_pos = f"{sel_row}{sel_col}" if sel_row else None

                def _on_select(row: str, col: str):
                    _selected_pos[0] = (row, col)
                    _render_grid(box_id, container, row, col)
                    page.update()

                grid = BoxGrid(
                    box=box, slots=slots, mode="select",
                    selected_position=sel_pos,
                    on_select=_on_select,
                )
                container.content = ft.Container(content=grid, padding=4)
            except Exception as ex:
                container.content = ft.Text(f"加载网格失败：{ex}",
                                             color=ft.Colors.RED_400)

        def _selected_occupied_slot():
            if _selected_box_id[0] is None or _selected_pos[0] is None:
                return None
            row, col = _selected_pos[0]
            try:
                slots_data = data_provider.get_slots(_selected_box_id[0])
            except Exception:
                return None
            for s in slots_data:
                slot = _to_slot(s)
                if slot.row_label == row and slot.col_label == col:
                    return slot
            return None

        def _do_register():
            row, col = _selected_pos[0]
            data_provider.register_storage_item(
                exp_id=experiment_id,
                item_id=item.id,
                box_id=_selected_box_id[0],
                row_label=row,
                col_label=col,
                notes=notes_tf.value,
            )

        def _save(_):
            if _selected_box_id[0] is None:
                _show_snack("请选择 Box", error=True)
                return
            if _selected_pos[0] is None:
                _show_snack("请在网格中选择位置", error=True)
                return
            row, col = _selected_pos[0]
            try:
                occupied = _selected_occupied_slot()
                if occupied and (
                    occupied.experiment_id != experiment_id
                    or occupied.sample_name != item.item_label
                ):
                    _confirm_overwrite(occupied, _do_register, dlg)
                else:
                    _do_register()
                    _close_overlay(page, dlg)
                    _refresh_items_list()
                    _open_next_unregistered()
            except Exception as ex:
                _show_snack(f"登记失败：{ex}", error=True)

        dlg = ft.AlertDialog(
            title=ft.Text(f"登记：{item.item_label}"),
            content=ft.Container(
                content=ft.Column([
                    ft.Text(f"管型：{item.tube_type or '—'}",
                            size=12, color=ft.Colors.GREY_600),
                    dd_box,
                    grid_container,
                    notes_tf,
                ], spacing=10, scroll=ft.ScrollMode.AUTO),
                width=min(page.width - 40, 420) if page.width else 380,
                height=500,
            ),
            actions=[
                ft.TextButton("跳过", on_click=lambda _: _close_dlg(dlg)),
                ft.ElevatedButton("确认登记", on_click=_save,
                                   bgcolor=ft.Colors.ORANGE_600,
                                   color=ft.Colors.WHITE),
            ],
        )
        _open_overlay(page, dlg)

    def _confirm_overwrite(slot: BoxSlot, register_fn: Callable[[], None], parent_dlg):
        def _confirm(_):
            try:
                register_fn()
                _close_overlay(page, confirm_dlg)
                _close_overlay(page, parent_dlg)
                _refresh_items_list()
                _open_next_unregistered()
            except Exception as ex:
                _show_snack(f"覆盖失败：{ex}", error=True)

        confirm_dlg = ft.AlertDialog(
            title=ft.Text("确认覆盖槽位"),
            content=ft.Text(
                f"{slot.position} 已有样品「{slot.sample_name}」。确认用当前样品覆盖这个位置吗？"
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _: _close_overlay(page, confirm_dlg)),
                ft.ElevatedButton("确认覆盖", on_click=_confirm,
                                   bgcolor=ft.Colors.RED_600,
                                   color=ft.Colors.WHITE),
            ],
        )
        _open_overlay(page, confirm_dlg)

    def _open_next_unregistered():
        try:
            fresh = data_provider.get_storage_items(experiment_id)
            unregistered = [_to_storage_item(d) for d in fresh
                            if not _to_storage_item(d).is_registered]
            if unregistered:
                _open_item_modal(unregistered[0])
            else:
                _show_snack("所有样品已登记完成！")
        except Exception:
            pass

    # ── Items list ───────────────────────────────
    items_list_col = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO)

    def _refresh_items_list():
        items_list_col.controls.clear()
        try:
            fresh_data = data_provider.get_storage_items(experiment_id)
            fresh = [_to_storage_item(d) for d in fresh_data]
        except Exception:
            fresh = items

        for item in fresh:
            registered = item.is_registered
            box_name = boxes_by_id.get(item.box_id, None)
            loc_text = f"{box_name.box_name} · {item.position}" if registered and box_name else "未登记"

            items_list_col.controls.append(ft.Container(
                content=ft.Row([
                    ft.Icon(
                        ft.Icons.CHECK_CIRCLE if registered else ft.Icons.RADIO_BUTTON_UNCHECKED,
                        color=ft.Colors.GREEN_600 if registered else ft.Colors.GREY_400,
                        size=20,
                    ),
                    ft.Column([
                        ft.Text(item.item_label, size=14,
                                weight=ft.FontWeight.W_500),
                        ft.Text(loc_text, size=12, color=ft.Colors.GREY_500),
                    ], expand=True, spacing=2),
                    ft.TextButton(
                        "登记" if not registered else "修改",
                        on_click=lambda _, i=item: _open_item_modal(i),
                        style=ft.ButtonStyle(
                            color=ft.Colors.ORANGE_600 if not registered
                            else ft.Colors.GREY_500
                        ),
                    ),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                border=ft.Border.all(1, ft.Colors.GREY_200),
                border_radius=8,
                padding=10,
                bgcolor=ft.Colors.GREEN_50 if registered else ft.Colors.WHITE,
            ))
        try:
            page.update()
        except Exception:
            pass

    _refresh_items_list()
    _auto_opened: list[bool] = [False]

    # ── Header ──────────────────────────────────
    def _finish_and_report(_):
        try:
            fresh = data_provider.get_storage_items(experiment_id)
            pending_storage = [_to_storage_item(d) for d in fresh if not _to_storage_item(d).is_registered]
            pending_photos = data_provider.get_pending_photos(experiment_id)
            if pending_storage or pending_photos:
                _show_wrapup_warning(len(pending_storage), len(pending_photos))
                return
            data_provider.update_experiment(experiment_id, status="completed")
        except Exception:
            pass
        on_done()

    def _show_wrapup_warning(storage_count: int, photo_count: int):
        parts = []
        if storage_count:
            parts.append(f"{storage_count} 个样品未登记")
        if photo_count:
            parts.append(f"{photo_count} 个拍照步骤待补")
        msg = "，".join(parts) or "还有收尾项未完成"
        dlg = ft.AlertDialog(
            title=ft.Text("收尾尚未完成"),
            content=ft.Text(f"{msg}。可以先查看报告，但实验会保持“待收尾”状态。"),
            actions=[
                ft.TextButton("继续收尾", on_click=lambda _: _close_overlay(page, dlg)),
                ft.ElevatedButton("先看报告", on_click=lambda _: (_close_overlay(page, dlg), on_done()),
                                  bgcolor=ft.Colors.ORANGE_600,
                                  color=ft.Colors.WHITE),
            ],
        )
        _open_overlay(page, dlg)

    header = ft.Row([
        ft.Text("存储登记", size=18, weight=ft.FontWeight.BOLD),
        ft.Container(expand=True),
        ft.TextButton("跳过", on_click=lambda _: on_skip(),
                      style=ft.ButtonStyle(color=ft.Colors.GREY_500)),
        ft.ElevatedButton("完成，查看报告", on_click=_finish_and_report,
                           bgcolor=ft.Colors.ORANGE_600, color=ft.Colors.WHITE),
    ])

    subtitle = ft.Text(
        "请为每个样品选择存储位置",
        size=13, color=ft.Colors.GREY_600,
    )

    def _close_dlg(dlg):
        _close_overlay(page, dlg)

    def _show_snack(msg: str, error: bool = False):
        _open_overlay(page, ft.SnackBar(            content=ft.Text(msg),            bgcolor=ft.Colors.RED_400 if error else ft.Colors.GREEN_600,        ))

    class BoxCheckinRoot(ft.Column):
        def did_mount(self) -> None:
            if not _auto_opened[0]:
                _auto_opened[0] = True
                _open_next_unregistered()

    return BoxCheckinRoot([
        ft.Container(
            content=ft.Column([header, subtitle], spacing=4),
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.GREY_200)),
        ),
        ft.Container(
            content=items_list_col,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            expand=True,
        ),
    ], expand=True, spacing=0)


def _to_slot(d) -> BoxSlot:
    if isinstance(d, BoxSlot):
        return d
    return BoxSlot(
        id=d["id"], box_id=d["box_id"],
        row_label=d["row_label"], col_label=d["col_label"],
        sample_name=d.get("sample_name", ""),
        notes=d.get("notes", ""),
        experiment_id=d.get("experiment_id"),
        step_id=d.get("step_id"),
        created_at=d.get("created_at", ""),
    )
