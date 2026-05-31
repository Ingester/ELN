"""
ELN App — StepCard
Full-screen card for a single experiment step.
Contains: title, Markdown description,
          timer widget, field editor, camera widget, complete button.
"""

from __future__ import annotations
import asyncio
import json
import os
import flet as ft
from typing import Callable, Optional

from db.models import Step
from components.timer_widget import TimerWidget
from components.field_editor import FieldEditor
from components.camera_widget import CameraWidget

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



# Desktop complete button width
_DOUBLE_WIDTH = 240


class StepCard(ft.Container):
    """
    step: Step model
    total_steps: total number of steps in experiment
    on_complete: called when user marks step complete (passes updated step data)
    on_prev / on_next: navigation callbacks
    is_mobile: layout mode
    data_provider: db.database or utils.api_client module
    """

    def __init__(
        self,
        step: Step,
        total_steps: int,
        on_complete: Optional[Callable[[dict], None]] = None,
        on_prev: Optional[Callable[[], None]] = None,
        on_next: Optional[Callable[[], None]] = None,
        on_add_storage: Optional[Callable[[], None]] = None,
        on_checkin_storage: Optional[Callable[[], None]] = None,
        is_mobile: bool = True,
        data_provider=None,
    ):
        super().__init__(
            padding=ft.Padding.all(16) if is_mobile else ft.Padding.all(24),
            expand=True,
        )
        self.step = step
        self.total_steps = total_steps
        self.on_complete = on_complete
        self.on_prev = on_prev
        self.on_next = on_next
        self.on_add_storage = on_add_storage
        self.on_checkin_storage = on_checkin_storage
        self.is_mobile = is_mobile
        self.data_provider = data_provider

        self._values = self._json_safe_dict(step.get_values())
        self._last_saved_values = dict(self._values)
        self._overrides = self._json_safe_dict(step.get_description_overrides())
        self._photo_pending = step.photo_pending
        self._completed = step.completed_at is not None
        self._mounted = False

        # Timer: only if step has a timer
        self._has_timer = step.effective_timer_seconds > 0
        self._timer_confirmed = False

        self._build_content()

    def did_mount(self) -> None:
        self._mounted = True
        if hasattr(self, "_field_editor"):
            try:
                self.page.run_task(self._autosave_loop)
            except Exception:
                pass

    def did_unmount(self) -> None:
        self._mounted = False

    async def _autosave_loop(self) -> None:
        while self._mounted:
            await asyncio.sleep(2)
            try:
                self._autosave_fields_once()
            except Exception:
                pass

    def _autosave_fields_once(self) -> None:
        if self.data_provider is None or not hasattr(self, "_field_editor"):
            return
        values = self._json_safe_dict(self._field_editor.get_values())
        if values == self._last_saved_values:
            return
        self._values = values
        self._save_step(silent=True)

    def _build_content(self) -> None:
        step = self.step
        idx = step.step_index
        total = self.total_steps

        # ── Progress indicator ──────────────────
        progress_text = ft.Text(
            f"Step {idx + 1} / {total}",
            size=12, color=ft.Colors.GREY_500,
        )

        # ── Title ───────────────────────────────
        title = ft.TextButton(
            step.title,
            on_click=self._open_step_title_editor,
            style=ft.ButtonStyle(
                color=ft.Colors.BLACK,
                padding=ft.Padding.symmetric(horizontal=0, vertical=2),
            ),
        )

        # ── Description with Markdown rendering ─
        self._editable_desc = ft.Markdown(
            value=step.description or "",
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            code_theme="atom-one-light",
        )
        desc_section = ft.Container(
            content=self._editable_desc,
            on_click=self._open_description_editor,
            padding=ft.Padding.symmetric(vertical=4),
        )

        # ── Timer ────────────────────────────────
        timer_section = ft.Container()
        if self._has_timer:
            self._timer_widget = TimerWidget(
                experiment_id=step.experiment_id,
                step_id=step.id,
                total_seconds=step.effective_timer_seconds,
                on_confirm=self._on_timer_confirmed,
                is_mobile=self.is_mobile,
            )
            timer_section = ft.Container(
                content=self._timer_widget,
                border=ft.Border.all(1, ft.Colors.ORANGE_200),
                border_radius=8,
                padding=8,
                margin=ft.Margin.symmetric(vertical=8),
            )

        # ── Field editor ─────────────────────────
        fields = step.get_fields()
        field_section = ft.Container()
        if fields:
            self._draft_status = ft.Text("", size=12, color=ft.Colors.GREY_600)
            self._field_editor = FieldEditor(
                fields=fields,
                values=self._values,
                on_change=self._on_values_change,
                is_mobile=self.is_mobile,
            )
            record_title = (
                ft.Text("记录数据", size=14, weight=ft.FontWeight.W_500,
                        color=ft.Colors.GREY_700)
                if os.environ.get("ELN_WEB_MODE") == "1"
                else ft.TextButton("记录数据", on_click=self._open_records_editor,
                                   style=ft.ButtonStyle(
                                       color=ft.Colors.GREY_700,
                                       padding=ft.Padding.all(0),
                                   ))
            )
            field_section = ft.Container(
                content=ft.Column([
                    ft.Row([
                        record_title,
                        ft.Container(expand=True),
                        ft.TextButton("保存填写", on_click=self._on_save_draft_click),
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    self._field_editor,
                    self._draft_status,
                ], spacing=8),
                border=ft.Border.all(1, ft.Colors.GREY_200),
                border_radius=8,
                padding=12,
                margin=ft.Margin.symmetric(vertical=4),
            )

        # ── Camera ───────────────────────────────
        camera_section = ft.Container()
        if step.has_camera:
            self._camera_widget = CameraWidget(
                step_id=step.id,
                experiment_id=step.experiment_id,
                existing_paths=step.get_photo_paths(),
                camera_required=step.camera_required,
                on_photo_added=self._on_photo_added,
                on_skip=self._on_photo_skip,
                is_mobile=self.is_mobile,
                data_provider=self.data_provider,
            )
            camera_section = ft.Container(
                content=ft.Column([
                    ft.Text("拍照记录", size=14, weight=ft.FontWeight.W_500,
                            color=ft.Colors.GREY_700),
                    self._camera_widget,
                ], spacing=8),
                border=ft.Border.all(1, ft.Colors.GREY_200),
                border_radius=8,
                padding=12,
                margin=ft.Margin.symmetric(vertical=4),
            )

        # ── Final wrap-up storage section ────────
        storage_section = ft.Container()
        if idx == total - 1:
            storage_section = ft.Container(
                content=ft.Column([
                    ft.Text("储存物品", size=14, weight=ft.FontWeight.W_500,
                            color=ft.Colors.GREY_700),
                    ft.Text(
                        "如果本次实验有样品、膜、胶、产物或其他物品需要保存，可以在这里添加并登记 Box 位置。",
                        size=12, color=ft.Colors.GREY_600,
                    ),
                    ft.Row([
                        ft.ElevatedButton(
                            "添加储存物品",
                            on_click=lambda _: self.on_add_storage() if self.on_add_storage else None,
                            bgcolor=ft.Colors.ORANGE_600,
                            color=ft.Colors.WHITE,
                        ),
                        ft.OutlinedButton(
                            "登记位置",
                            on_click=lambda _: self.on_checkin_storage() if self.on_checkin_storage else None,
                        ),
                    ], spacing=8, wrap=True),
                ], spacing=8),
                border=ft.Border.all(1, ft.Colors.ORANGE_200),
                border_radius=8,
                padding=12,
                margin=ft.Margin.symmetric(vertical=4),
            )

        # ── Complete button ───────────────────────
        self._action_status = ft.Text("", size=12, color=ft.Colors.GREY_600)
        self._btn_complete = ft.ElevatedButton(
            "✅ 完成此步骤" if not self._completed else "已完成",
            on_click=self._on_complete_click,
            bgcolor=ft.Colors.GREEN_600 if not self._completed else ft.Colors.GREY_400,
            color=ft.Colors.WHITE,
            disabled=self._completed,
            width=_DOUBLE_WIDTH if not self.is_mobile else None,
        )

        # ── Navigation ────────────────────────────
        nav_row = ft.Row([
            ft.IconButton(
                ft.Icons.ARROW_BACK_IOS,
                on_click=lambda _: self.on_prev() if self.on_prev else None,
                disabled=idx == 0,
                tooltip="上一步",
            ),
            ft.Container(expand=True),
            ft.IconButton(
                ft.Icons.ARROW_FORWARD_IOS,
                on_click=self._on_next_click,
                disabled=self._next_disabled(),
                tooltip="下一步",
            ),
        ])

        # ── Completed overlay ─────────────────────
        completed_banner = ft.Container()
        if self._completed:
            completed_banner = ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN_600),
                    ft.Text("此步骤已完成", color=ft.Colors.GREEN_700,
                            weight=ft.FontWeight.W_500),
                ], spacing=6),
                bgcolor=ft.Colors.GREEN_50,
                border_radius=6,
                padding=8,
            )

        # ── Assemble ──────────────────────────────
        self.content = ft.Column([
            progress_text,
            title,
            ft.Divider(height=1, color=ft.Colors.GREY_200),
            desc_section,
            timer_section,
            field_section,
            camera_section,
            storage_section,
            completed_banner,
            self._action_status,
            self._btn_complete,
            nav_row,
        ], spacing=10, scroll=ft.ScrollMode.AUTO)

    # ── Callbacks ──────────────────────────────

    def _open_step_title_editor(self, _) -> None:
        tf = ft.TextField(label="步骤标题", value=self.step.title, width=480, autofocus=True)
        err = ft.Text("", size=12, color=ft.Colors.RED_600)

        def _save(_):
            title = (tf.value or "").strip()
            if not title:
                err.value = "标题不能为空"
                self.page.update()
                return
            try:
                self.data_provider.update_step(self.step.id, title=title)
                self.step.title = title
                _close_overlay(self.page, dlg)
                self._build_content()
                self.page.update()
            except Exception as ex:
                err.value = f"保存失败：{ex}"
                self.page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("编辑步骤标题"),
            content=ft.Column([tf, err], tight=True, spacing=8),
            actions=[
                ft.TextButton("取消", on_click=lambda _: _close_overlay(self.page, dlg)),
                ft.ElevatedButton("保存", on_click=_save),
            ],
        )
        _open_overlay(self.page, dlg)

    def _open_description_editor(self, _) -> None:
        tf = ft.TextField(
            label="步骤说明",
            value=self.step.description,
            multiline=True,
            min_lines=8,
            max_lines=14,
            width=620,
            autofocus=True,
        )
        err = ft.Text("", size=12, color=ft.Colors.RED_600)

        def _save(_):
            desc = tf.value or ""
            try:
                self.data_provider.update_step(
                    self.step.id,
                    description=desc,
                    description_overrides_json="{}",
                )
                self.step.description = desc
                self.step.description_overrides_json = "{}"
                self._overrides = {}
                _close_overlay(self.page, dlg)
                self._build_content()
                self.page.update()
            except Exception as ex:
                err.value = f"保存失败：{ex}"
                self.page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("编辑步骤说明"),
            content=ft.Column([
                ft.Text("保存后会清空本步骤已有的数字覆盖值，直接使用这段新说明。", size=12, color=ft.Colors.GREY_600),
                tf,
                err,
            ], tight=True, spacing=8),
            actions=[
                ft.TextButton("取消", on_click=lambda _: _close_overlay(self.page, dlg)),
                ft.ElevatedButton("保存", on_click=_save),
            ],
        )
        _open_overlay(self.page, dlg)

    def _open_records_editor(self, _) -> None:
        self._sync_field_values()
        fields = self.step.get_fields()
        lines = []
        for f in fields:
            options = ",".join(f.options or [])
            value = self._values.get(f.key, f.default)
            lines.append(
                f"{f.key} | {f.label} | {f.type} | {str(f.required).lower()} | {options} | {value}"
            )
        tf = ft.TextField(
            label="记录字段",
            value="\n".join(lines),
            multiline=True,
            min_lines=8,
            max_lines=14,
            width=760,
            autofocus=True,
        )
        err = ft.Text("", size=12, color=ft.Colors.RED_600)

        def _parse():
            parsed_fields = []
            parsed_values = {}
            for raw_line in (tf.value or "").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                while len(parts) < 6:
                    parts.append("")
                key, label, field_type, required, options, value = parts[:6]
                if not key or not label:
                    raise ValueError("每一行至少需要 key 和 label")
                field_type = field_type or "text"
                if field_type not in ("text", "number", "dropdown"):
                    raise ValueError(f"{key} 的 type 只能是 text、number、dropdown")
                parsed_fields.append({
                    "key": key,
                    "label": label,
                    "type": field_type,
                    "default": "",
                    "required": required.lower() in ("true", "1", "yes", "y", "必填"),
                    "options": [o.strip() for o in options.split(",") if o.strip()],
                })
                parsed_values[key] = value
            return parsed_fields, parsed_values

        def _save(_):
            try:
                fields_json, values = _parse()
                self.data_provider.update_step(
                    self.step.id,
                    fields_json=json.dumps(fields_json, ensure_ascii=False),
                    values_json=json.dumps(values, ensure_ascii=False),
                )
                self.step.fields_json = json.dumps(fields_json, ensure_ascii=False)
                self.step.values_json = json.dumps(values, ensure_ascii=False)
                self._values = self._json_safe_dict(values)
                _close_overlay(self.page, dlg)
                self._build_content()
                self.page.update()
            except Exception as ex:
                err.value = f"保存失败：{ex}"
                self.page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("编辑记录数据"),
            content=ft.Column([
                ft.Text(
                    "一行一个字段：key | label | type | required | options逗号分隔 | 当前值。"
                    "新增备注可写：notes | 备注 | text | false | | 这里写备注",
                    size=12, color=ft.Colors.GREY_600,
                ),
                tf,
                err,
            ], tight=True, spacing=8),
            actions=[
                ft.TextButton("取消", on_click=lambda _: _close_overlay(self.page, dlg)),
                ft.ElevatedButton("保存", on_click=_save),
            ],
        )
        _open_overlay(self.page, dlg)

    def _on_overrides_change(self, new_overrides: dict) -> None:
        self._overrides = self._json_safe_dict(new_overrides)
        self._save_step()
        if hasattr(self, "_action_status"):
            self._action_status.value = "描述参数已自动保存"
            self._action_status.color = ft.Colors.GREEN_700

    def _on_values_change(self, new_values: dict) -> None:
        self._values = self._json_safe_dict(new_values)
        self._save_step()
        if hasattr(self, "_draft_status"):
            self._draft_status.value = "记录值已自动保存"
            self._draft_status.color = ft.Colors.GREEN_700

    def _on_photo_added(self, rel_path: str) -> None:
        self._photo_pending = False
        self._save_step()

    def _on_photo_skip(self) -> None:
        self._photo_pending = True
        self._save_step()

    def _on_timer_confirmed(self) -> None:
        self._timer_confirmed = True
        try:
            self.update()
        except Exception:
            pass

    def _on_complete_click(self, _) -> None:
        # Validate required fields
        if hasattr(self, "_field_editor"):
            self._sync_field_values()
            errors = self._field_editor.validate()
            if errors:
                self._show_snack("\n".join(errors), error=True)
                return
        else:
            missing = [
                f"「{f.label}」为必填项"
                for f in self.step.get_fields()
                if f.required and not str(self._values.get(f.key, f.default) or "").strip()
            ]
            if missing:
                self._show_snack("\n".join(missing), error=True)
                return

        # If timer exists and is in overtime, must confirm first
        if self._has_timer and not self._timer_confirmed:
            from timer_manager import get_timer_manager
            tm = get_timer_manager()
            state = tm.get_state(self.step.experiment_id, self.step.id)
            if state and state.status == "overtime":
                self._show_snack("请先点击「确认，继续下一步」完成计时", error=True)
                return

        if not self._save_step(complete=True):
            return
        self._completed = True
        self._btn_complete.text = "已完成"
        self._btn_complete.bgcolor = ft.Colors.GREY_400
        self._btn_complete.disabled = True
        self.update()

        if self.on_complete:
            self.on_complete({
                "values_json": json.dumps(self._json_safe_dict(self._values), ensure_ascii=False),
                "description_overrides_json": json.dumps(self._json_safe_dict(self._overrides), ensure_ascii=False),
                "photo_pending": int(self._photo_pending),
            })

    def _on_next_click(self, _) -> None:
        if self._next_disabled():
            return
        if self.on_next:
            self.on_next()

    def _next_disabled(self) -> bool:
        """Next is disabled if timer is in overtime (must confirm first)."""
        if self._has_timer:
            from timer_manager import get_timer_manager
            tm = get_timer_manager()
            state = tm.get_state(self.step.experiment_id, self.step.id)
            if state and state.status == "overtime":
                return True
        return False

    def _save_step(self, complete: bool = False, silent: bool = False) -> bool:
        """Persist current field values and overrides."""
        if self.data_provider is None:
            return True
        try:
            self._values = self._json_safe_dict(self._values)
            self._overrides = self._json_safe_dict(self._overrides)
            kwargs = {
                "values_json": json.dumps(self._values, ensure_ascii=False),
                "description_overrides_json": json.dumps(self._overrides, ensure_ascii=False),
                "photo_pending": int(self._photo_pending),
            }
            self.data_provider.update_step(self.step.id, **kwargs)
            if complete:
                self.data_provider.complete_step(self.step.id)
            self._last_saved_values = dict(self._values)
            return True
        except Exception as e:
            if not silent:
                self._show_snack(f"保存失败：{e}", error=True)
            return False

    def persist_draft(self) -> bool:
        """Save current field values without changing completion state."""
        self._sync_field_values()
        return self._save_step()

    def _sync_field_values(self) -> None:
        if hasattr(self, "_field_editor"):
            self._values = self._json_safe_dict(self._field_editor.get_values())

    def _on_save_draft_click(self, _) -> None:
        if not self.persist_draft():
            return
        if hasattr(self, "_draft_status"):
            self._draft_status.value = "已保存填写内容"
            self._draft_status.color = ft.Colors.GREEN_700
            try:
                self.update()
            except Exception:
                pass

    @staticmethod
    def _json_safe_dict(data: dict) -> dict[str, str]:
        safe = {}
        for key, value in (data or {}).items():
            if value is None:
                safe[str(key)] = ""
            elif isinstance(value, (str, int, float, bool)):
                safe[str(key)] = str(value)
            elif hasattr(value, "value") and isinstance(value.value, (str, int, float, bool)):
                safe[str(key)] = str(value.value)
            else:
                safe[str(key)] = str(value)
        return safe

    def _show_snack(self, msg: str, error: bool = False) -> None:
        self._show_inline_message(msg, error=error)

    def _show_inline_message(self, msg: str, error: bool = False) -> None:
        try:
            if hasattr(self, "_action_status"):
                self._action_status.value = msg
                self._action_status.color = ft.Colors.RED_600 if error else ft.Colors.GREEN_700
                self.update()
        except Exception:
            pass
