"""
ELN App — Protocol Editor View
Full CRUD editor for a protocol template.
Supports: rename, add/remove/reorder steps, edit step fields,
          edit storage_items, save to library.
"""

from __future__ import annotations
import json
import flet as ft
from typing import Callable, Optional

from db.models import ProtocolDefinition, ProtocolStep, ProtocolField, StorageItemTemplate

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




def build_protocol_editor_view(
    page: ft.Page,
    data_provider,
    protocol_id: Optional[int],          # None = new protocol
    initial_json: Optional[str] = None,  # pre-filled JSON (from import)
    on_save: Optional[Callable[[int], None]] = None,   # called with saved protocol id
    on_cancel: Optional[Callable[[], None]] = None,
    is_mobile: bool = True,
) -> ft.Control:

    # ── Load or init protocol ───────────────────
    if protocol_id is not None:
        try:
            p = data_provider.get_protocol(protocol_id)
            pdef = ProtocolDefinition.from_json(
                p["protocol_json"] if isinstance(p, dict) else p.protocol_json
            )
        except Exception:
            pdef = ProtocolDefinition(protocol_name="新协议")
    elif initial_json:
        try:
            pdef = ProtocolDefinition.from_json(initial_json)
        except Exception:
            pdef = ProtocolDefinition(protocol_name="新协议")
    else:
        pdef = ProtocolDefinition(protocol_name="新协议")

    # ── State ───────────────────────────────────
    _steps: list[ProtocolStep] = list(pdef.steps)
    _storage_items: list[StorageItemTemplate] = list(pdef.storage_items)
    _selected_step_idx: list[int] = [0]  # mutable ref

    # ── Top fields ──────────────────────────────
    tf_name = ft.TextField(
        value=pdef.protocol_name, label="协议名称",
        border_color=ft.Colors.ORANGE_300,
        focused_border_color=ft.Colors.ORANGE_600,
    )
    tf_version = ft.TextField(
        value=pdef.version, label="版本", width=100,
        border_color=ft.Colors.ORANGE_300,
    )
    tf_author = ft.TextField(
        value=pdef.author, label="作者",
        border_color=ft.Colors.ORANGE_300,
    )

    # ── Step list (left panel on desktop, top list on mobile) ──
    steps_list_col = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=4)
    step_detail_col = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=8)

    def _refresh_steps_list():
        steps_list_col.controls.clear()
        for i, step in enumerate(_steps):
            is_sel = (i == _selected_step_idx[0])
            steps_list_col.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Text(f"{i+1}. {step.title or '(无标题)'}",
                                size=13, expand=True,
                                color=ft.Colors.WHITE if is_sel else ft.Colors.BLACK),
                        ft.Row([
                            ft.IconButton(
                                ft.Icons.ARROW_UPWARD, icon_size=16,
                                on_click=lambda _, idx=i: _move_step(idx, -1),
                                disabled=i == 0,
                                icon_color=ft.Colors.WHITE if is_sel else ft.Colors.GREY_500,
                            ),
                            ft.IconButton(
                                ft.Icons.ARROW_DOWNWARD, icon_size=16,
                                on_click=lambda _, idx=i: _move_step(idx, 1),
                                disabled=i == len(_steps) - 1,
                                icon_color=ft.Colors.WHITE if is_sel else ft.Colors.GREY_500,
                            ),
                            ft.IconButton(
                                ft.Icons.DELETE_OUTLINE, icon_size=16,
                                on_click=lambda _, idx=i: _delete_step(idx),
                                icon_color=ft.Colors.RED_300 if is_sel else ft.Colors.RED_400,
                            ),
                        ], spacing=0),
                    ]),
                    bgcolor=ft.Colors.ORANGE_600 if is_sel else ft.Colors.GREY_100,
                    border_radius=6,
                    padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                    on_click=lambda _, idx=i: _select_step(idx),
                )
            )
        try:
            page.update()
        except Exception:
            pass

    def _select_step(idx: int):
        if not (0 <= idx < len(_steps)):
            return
        _selected_step_idx[0] = idx
        _refresh_steps_list()
        _refresh_step_detail()

    def _move_step(idx: int, direction: int):
        new_idx = idx + direction
        if 0 <= new_idx < len(_steps):
            _steps[idx], _steps[new_idx] = _steps[new_idx], _steps[idx]
            _selected_step_idx[0] = new_idx
            _refresh_steps_list()
            _refresh_step_detail()

    def _delete_step(idx: int):
        if not (0 <= idx < len(_steps)):
            return
        if len(_steps) <= 1:
            _show_snack("至少保留一个步骤")
            return
        _steps.pop(idx)
        _selected_step_idx[0] = min(idx, len(_steps) - 1)
        _refresh_steps_list()
        _refresh_step_detail()

    def _add_step(_=None):
        _steps.append(ProtocolStep(title=f"Step {len(_steps)+1}", description=""))
        _selected_step_idx[0] = len(_steps) - 1
        _refresh_steps_list()
        _refresh_step_detail()

    # ── Step detail editor ──────────────────────
    def _refresh_step_detail():
        step_detail_col.controls.clear()
        if not _steps:
            return
        idx = min(max(_selected_step_idx[0], 0), len(_steps) - 1)
        _selected_step_idx[0] = idx
        step = _steps[idx]

        tf_title = ft.TextField(
            value=step.title, label="步骤标题",
            on_change=lambda e: _update_step_field(idx, "title", e.control.value),
            border_color=ft.Colors.ORANGE_300,
        )
        tf_desc = ft.TextField(
            value=step.description, label="步骤描述",
            multiline=True, min_lines=3, max_lines=6,
            on_change=lambda e: _update_step_field(idx, "description", e.control.value),
            border_color=ft.Colors.ORANGE_300,
        )
        tf_timer = ft.TextField(
            value=str(step.timer_seconds // 60) if step.timer_seconds else "0",
            label="计时时长（分钟，0=无计时）",
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=lambda e: _update_step_timer(idx, e.control.value),
            width=200,
            border_color=ft.Colors.ORANGE_300,
        )
        sw_camera = ft.Switch(
            label="需要拍照",
            value=step.has_camera,
            on_change=lambda e: _update_step_field(idx, "has_camera", e.control.value),
            active_color=ft.Colors.ORANGE_600,
        )
        sw_camera_req = ft.Switch(
            label="拍照必须（不可跳过）",
            value=step.camera_required,
            on_change=lambda e: _update_step_field(idx, "camera_required", e.control.value),
            active_color=ft.Colors.ORANGE_600,
        )

        # Fields editor
        fields_section = _build_fields_editor(idx, step)

        step_detail_col.controls.extend([
            ft.Text(f"编辑 Step {idx+1}", size=14, weight=ft.FontWeight.BOLD,
                    color=ft.Colors.ORANGE_700),
            tf_title, tf_desc,
            ft.Row([tf_timer, sw_camera, sw_camera_req], wrap=True, spacing=12),
            ft.Divider(height=1),
            fields_section,
        ])
        try:
            page.update()
        except Exception:
            pass

    def _update_step_field(idx: int, field: str, value):
        if 0 <= idx < len(_steps):
            setattr(_steps[idx], field, value)
            if field == "title":
                _refresh_steps_list()

    def _update_step_timer(idx: int, value: str):
        try:
            mins = float(value or "0")
            _steps[idx].timer_seconds = int(mins * 60)
        except ValueError:
            pass

    def _build_fields_editor(step_idx: int, step: ProtocolStep) -> ft.Column:
        fields_col = ft.Column(spacing=6)

        def _refresh_fields():
            fields_col.controls.clear()
            fields_col.controls.append(
                ft.Row([
                    ft.Text("字段", size=13, weight=ft.FontWeight.W_500),
                    ft.Container(expand=True),
                    ft.TextButton("+ 添加字段",
                                  on_click=lambda _: _add_field(step_idx),
                                  style=ft.ButtonStyle(color=ft.Colors.ORANGE_600)),
                ])
            )
            for fi, f in enumerate(step.fields):
                fields_col.controls.append(_build_field_row(step_idx, fi, f, _refresh_fields))
            try:
                page.update()
            except Exception:
                pass

        _refresh_fields()
        return fields_col

    def _build_field_row(step_idx: int, fi: int, f: ProtocolField,
                          refresh_cb: Callable) -> ft.Row:
        return ft.Row([
            ft.TextField(value=f.label, label="标签", width=120, dense=True,
                         on_change=lambda e: _update_field_attr(step_idx, fi, "label", e.control.value)),
            ft.Dropdown(
                value=f.type,
                options=[ft.dropdown.Option("text", "文本"),
                         ft.dropdown.Option("number", "数字"),
                         ft.dropdown.Option("dropdown", "下拉")],
                width=90, dense=True,
                on_select=lambda e: _update_field_attr(step_idx, fi, "type", e.control.value),
            ),
            ft.TextField(value=f.default, label="默认值", width=80, dense=True,
                         on_change=lambda e: _update_field_attr(step_idx, fi, "default", e.control.value)),
            ft.Checkbox(value=f.required, label="必填",
                        on_change=lambda e: _update_field_attr(step_idx, fi, "required", e.control.value)),
            ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=18,
                          icon_color=ft.Colors.RED_400,
                          on_click=lambda _, s=step_idx, i=fi: _delete_field(s, i, refresh_cb)),
        ], wrap=True, spacing=6)

    def _add_field(step_idx: int):
        if not (0 <= step_idx < len(_steps)):
            return
        _steps[step_idx].fields.append(
            ProtocolField(key=f"field_{len(_steps[step_idx].fields)}", label="新字段", type="text")
        )
        _refresh_step_detail()

    def _delete_field(step_idx: int, fi: int, refresh_cb: Callable):
        if not (0 <= step_idx < len(_steps)) or not (0 <= fi < len(_steps[step_idx].fields)):
            return
        _steps[step_idx].fields.pop(fi)
        _refresh_step_detail()

    def _update_field_attr(step_idx: int, fi: int, attr: str, value):
        if 0 <= step_idx < len(_steps) and 0 <= fi < len(_steps[step_idx].fields):
            setattr(_steps[step_idx].fields[fi], attr, value)
            # Auto-generate key from label
            if attr == "label":
                _steps[step_idx].fields[fi].key = value.lower().replace(" ", "_")

    # ── Save ────────────────────────────────────
    def _save(_=None):
        # Validate
        name = tf_name.value.strip()
        if not name:
            tf_name.error_text = "协议名称不能为空"
            page.update()
            return

        new_def = ProtocolDefinition(
            protocol_name=name,
            version=tf_version.value.strip() or "1.0",
            author=tf_author.value.strip(),
            steps=_steps,
            storage_items=_storage_items,
        )
        pjson = new_def.to_json()

        try:
            if protocol_id is not None:
                result = data_provider.update_protocol(protocol_id, pjson)
                saved_id = result["id"] if isinstance(result, dict) else result.id
            else:
                result = data_provider.create_protocol(pjson)
                saved_id = result["id"] if isinstance(result, dict) else result.id
            _show_snack("保存成功")
            if on_save:
                on_save(saved_id)
        except Exception as ex:
            _show_snack(f"保存失败：{ex}", error=True)

    def _show_snack(msg: str, error: bool = False):
        _open_overlay(page, ft.SnackBar(            content=ft.Text(msg),            bgcolor=ft.Colors.RED_400 if error else ft.Colors.GREEN_600,        ))

    # ── Initial render ──────────────────────────
    _refresh_steps_list()
    _refresh_step_detail()

    # ── Layout ──────────────────────────────────
    header = ft.Row([
        ft.IconButton(ft.Icons.ARROW_BACK,
                      on_click=lambda _: on_cancel() if on_cancel else None,
                      tooltip="返回"),
        ft.Text("协议编辑器", size=18, weight=ft.FontWeight.BOLD),
        ft.Container(expand=True),
        ft.ElevatedButton("保存", on_click=_save,
                           bgcolor=ft.Colors.ORANGE_600, color=ft.Colors.WHITE),
    ])

    meta_row = ft.Row([tf_name, tf_version, tf_author], wrap=True, spacing=12)

    steps_panel = ft.Column([
        ft.Row([
            ft.Text("步骤列表", size=13, weight=ft.FontWeight.W_500),
            ft.Container(expand=True),
            ft.IconButton(ft.Icons.ADD, on_click=_add_step,
                          tooltip="添加步骤", icon_color=ft.Colors.ORANGE_600),
        ]),
        steps_list_col,
    ], width=220 if not is_mobile else None)

    if is_mobile:
        body = ft.Column([
            meta_row,
            ft.Divider(),
            steps_panel,
            ft.Divider(),
            step_detail_col,
        ], scroll=ft.ScrollMode.AUTO, expand=True, spacing=12)
    else:
        body = ft.Row([
            ft.Container(content=steps_panel, width=240,
                         border=ft.Border.only(right=ft.BorderSide(1, ft.Colors.GREY_200)),
                         padding=12),
            ft.Container(
                content=ft.Column([meta_row, ft.Divider(), step_detail_col],
                                   scroll=ft.ScrollMode.AUTO, spacing=12),
                expand=True, padding=16,
            ),
        ], expand=True)

    return ft.Column([
        ft.Container(content=header,
                     padding=ft.Padding.symmetric(horizontal=8, vertical=8),
                     border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.GREY_200))),
        ft.Container(content=body, expand=True, padding=12),
    ], expand=True, spacing=0)
