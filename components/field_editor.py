"""
ELN App — FieldEditor
Renders a list of protocol fields as an editable form.
Supports text, number, and dropdown field types.
Keeps typing local while editing; values are read by the parent when saving.
"""

from __future__ import annotations
import os
import flet as ft
from typing import Callable, Optional

from db.models import ProtocolField
from components.web_text_controls import WebKeyboardInput


def _open_overlay(page, ctrl) -> None:
    if ctrl not in page.overlay:
        page.overlay.append(ctrl)
    ctrl.open = True
    page.update()


def _close_overlay(page, ctrl) -> None:
    ctrl.open = False
    page.update()


class FieldEditor(ft.Column):
    """
    fields: list of ProtocolField definitions
    values: current {key: value} dict
    on_change: called with updated values dict
    is_mobile: affects layout density
    """

    def __init__(
        self,
        fields: list[ProtocolField],
        values: dict[str, str],
        on_change: Optional[Callable[[dict[str, str]], None]] = None,
        is_mobile: bool = True,
    ):
        super().__init__(spacing=12)
        self.fields = fields
        incoming_values = values or {}
        self.values = {
            f.key: _safe_value(incoming_values.get(f.key, f.default))
            for f in fields
        }
        self.on_change = on_change
        self.is_mobile = is_mobile
        self._field_controls: dict[str, ft.Control] = {}
        self._values_dirty = False
        self._web_mode = os.environ.get("ELN_WEB_MODE") == "1"

        self._build_controls()

    def _build_controls(self) -> None:
        if not self.fields:
            self.controls = []
            return

        rows: list[ft.Control] = []
        for f in self.fields:
            ctrl = self._build_field(f)
            self._field_controls[f.key] = ctrl
            rows.append(ft.Column([
                ft.Text(
                    f.label + (" *" if f.required else ""),
                    size=13,
                    color=ft.Colors.GREY_700,
                    weight=ft.FontWeight.W_500,
                ),
                ctrl,
            ], spacing=4))

        self.controls = rows

    def _build_field(self, f: ProtocolField) -> ft.Control:
        current = _safe_value(self.values.get(f.key, f.default))

        if f.type == "dropdown":
            selected = current if current in f.options else (f.options[0] if f.options else None)
            self.values[f.key] = _safe_value(selected)
            if self._web_mode:
                return self._build_web_choice(f, selected)
            return ft.Dropdown(
                value=selected,
                options=[ft.dropdown.Option(o) for o in f.options],
                on_select=lambda e, key=f.key: self._capture_value(
                    key,
                    e.control.value,
                    notify=True,
                ),
                dense=True,
                width=None,
                key=f"field_{f.key}",
            )
        elif f.type == "number":
            # Keep protocol "number" semantics for validation/reporting, but render
            # as a plain text box. Flet Web number inputs can collapse the page when
            # lab-style values contain letters, signs, ranges, or sample IDs.
            return self._build_text_input(f.key, current)
        else:  # text
            return self._build_text_input(f.key, current)

    def _build_text_input(self, key: str, current: str) -> ft.Control:
        if self._web_mode:
            return WebKeyboardInput(
                value=current,
                on_change=lambda value, field_key=key: self._capture_value(
                    field_key,
                    value,
                ),
                on_submit=lambda value, field_key=key: self._capture_value(
                    field_key,
                    value,
                    notify=True,
                ),
                placeholder="点击后输入",
            )
        return ft.TextField(
            value=current,
            on_blur=lambda e, field_key=key: self._capture_value(
                field_key,
                e.control.value,
                notify=True,
            ),
            dense=True,
            border_color=ft.Colors.ORANGE_300,
            focused_border_color=ft.Colors.ORANGE_600,
            key=f"field_{key}",
        )

    def _build_web_choice(self, f: ProtocolField, selected: Optional[str]) -> ft.Control:
        selected_value = _safe_value(selected)
        buttons: list[ft.Control] = []

        def _style(option: str) -> ft.ButtonStyle:
            active = option == self.values.get(f.key, selected_value)
            return ft.ButtonStyle(
                bgcolor=ft.Colors.ORANGE_100 if active else ft.Colors.WHITE,
                color=ft.Colors.ORANGE_800 if active else ft.Colors.GREY_800,
                side=ft.BorderSide(
                    1,
                    ft.Colors.ORANGE_400 if active else ft.Colors.GREY_300,
                ),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            )

        def _select(option: str):
            def _handler(_):
                self._capture_value(f.key, option, notify=True)
                for btn in buttons:
                    btn.style = _style(str(btn.data))
                try:
                    row.update()
                except Exception:
                    pass
            return _handler

        for option in (f.options or []):
            buttons.append(
                ft.OutlinedButton(
                    content=option,
                    data=option,
                    on_click=_select(option),
                    style=_style(option),
                    height=34,
                )
            )

        row = ft.Row(buttons, spacing=8, wrap=True)
        return row

    def _capture_value(self, key: str, value, notify: bool = False) -> None:
        self.values[key] = _safe_value(value)
        self._values_dirty = True
        if notify and self.on_change:
            self.on_change(dict(self.values))

    def get_values(self) -> dict[str, str]:
        values = {k: _safe_value(v) for k, v in self.values.items()}
        for key, ctrl in self._field_controls.items():
            if hasattr(ctrl, "value"):
                values[key] = _safe_value(getattr(ctrl, "value", values.get(key, "")))
            else:
                values[key] = _safe_value(values.get(key, ""))
        self.values = values
        return dict(values)

    def set_values(self, values: dict[str, str]) -> None:
        """Update controls from an external source, such as phone sync."""
        incoming = values or {}
        for key, ctrl in self._field_controls.items():
            new_value = _safe_value(incoming.get(key, ""))
            if hasattr(ctrl, "value"):
                ctrl.value = new_value
            elif hasattr(ctrl, "content"):
                ctrl.content = new_value or "请选择"
                ctrl.data = new_value or "请选择"
            self.values[key] = new_value
        try:
            self.update()
        except Exception:
            pass

    def validate(self) -> list[str]:
        """Return list of validation error messages."""
        errors = []
        values = self.get_values()
        for f in self.fields:
            if f.required and not values.get(f.key, "").strip():
                errors.append(f"「{f.label}」为必填项")
        return errors


def _safe_value(value) -> str:
    """Keep only JSON-safe scalar text values from Flet events."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if hasattr(value, "value") and isinstance(value.value, (str, int, float, bool)):
        return str(value.value)
    return str(value)
