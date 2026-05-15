"""
ELN App — EditableText
Renders step description with inline-editable numbers.
Numbers with lab units appear as orange underlined spans.
Tapping opens an edit dialog (BottomSheet on mobile, AlertDialog on desktop).
"""

from __future__ import annotations
import flet as ft
from typing import Callable, Optional

from utils.inline_editor import parse_description, apply_override, TextSegment

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




class EditableText(ft.Column):
    """
    description: raw step description string
    overrides: current {override_key: new_value} dict
    on_change: called with updated overrides dict when user edits a number
    is_mobile: use BottomSheet (True) or AlertDialog (False)
    """

    def __init__(
        self,
        description: str,
        overrides: dict[str, str],
        on_change: Optional[Callable[[dict[str, str]], None]] = None,
        is_mobile: bool = True,
        text_size: int = 15,
    ):
        super().__init__(spacing=0)
        self.description = description
        self.overrides = dict(overrides)
        self.on_change = on_change
        self.is_mobile = is_mobile
        self.text_size = text_size

        self.controls = [self._build_text()]

    def _build_text(self) -> ft.Control:
        segments = parse_description(self.description, self.overrides)
        spans: list[ft.TextSpan] = []

        for seg in segments:
            if seg.is_editable:
                spans.append(ft.TextSpan(
                    text=seg.text,
                    style=ft.TextStyle(
                        color=ft.Colors.ORANGE_700,
                        decoration=ft.TextDecoration.UNDERLINE,
                        decoration_color=ft.Colors.ORANGE_400,
                        weight=ft.FontWeight.W_500,
                        size=self.text_size,
                    ),
                    on_click=lambda e, s=seg: self._open_editor(s),
                ))
            else:
                spans.append(ft.TextSpan(
                    text=seg.text,
                    style=ft.TextStyle(size=self.text_size),
                ))

        return ft.Text(spans=spans, selectable=True)

    def _open_editor(self, seg: TextSegment) -> None:
        current = self.overrides.get(seg.override_key, seg.number_value)
        unit_label = seg.unit if seg.unit else ""

        tf = ft.TextField(
            value=current,
            label=f"修改数值（{unit_label}）" if unit_label else "修改数值",
            keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True,
            suffix=unit_label,
            width=220,
        )
        error_text = ft.Text("", color=ft.Colors.RED_400, size=12)

        def _save(_):
            val = tf.value.strip()
            try:
                new_overrides = apply_override(
                    self.description, seg.override_key, val, self.overrides
                )
                self.overrides = new_overrides
                if self.on_change:
                    self.on_change(self.overrides)
                _close()
                self._rebuild()
            except ValueError as e:
                error_text.value = str(e)
                self.page.update()

        def _close():
            if self.is_mobile and hasattr(self, "_bottom_sheet"):
                self._bottom_sheet.open = False
            else:
                if hasattr(self, "_dialog"):
                    self._dialog.open = False
            self.page.update()

        if self.is_mobile:
            self._bottom_sheet = ft.BottomSheet(
                content=ft.Container(
                    content=ft.Column([
                        ft.Text("编辑数值", size=16, weight=ft.FontWeight.BOLD),
                        ft.Text(
                            f"原始值：{seg.number_value}{seg.unit}",
                            size=12, color=ft.Colors.GREY_600,
                        ),
                        tf,
                        error_text,
                        ft.Row([
                            ft.TextButton("取消", on_click=lambda _: _close()),
                            ft.ElevatedButton("确定", on_click=_save,
                                               bgcolor=ft.Colors.ORANGE_600,
                                               color=ft.Colors.WHITE),
                        ], alignment=ft.MainAxisAlignment.END),
                    ], spacing=12),
                    padding=20,
                ),
                open=True,
            )
            self.page.overlay.append(self._bottom_sheet)
            self.page.update()
        else:
            self._dialog = ft.AlertDialog(
                title=ft.Text(f"编辑：{seg.override_key}"),
                content=ft.Column([tf, error_text], tight=True),
                actions=[
                    ft.TextButton("取消", on_click=lambda _: _close()),
                    ft.ElevatedButton("确定", on_click=_save),
                ],
                open=True,
            )
            _open_overlay(self.page, self._dialog)
            self.page.update()

    def _rebuild(self) -> None:
        """Replace the rendered text control in place."""
        self.controls = [self._build_text()]
        try:
            self.update()
        except Exception:
            pass

    def update_description(self, description: str,
                            overrides: dict[str, str]) -> None:
        """Called externally to refresh content."""
        self.description = description
        self.overrides = dict(overrides)
        self._rebuild()
