"""
Small text-editing controls that avoid Flet TextField on web.

Flet Web text inputs currently blank the Flutter canvas on this app after some
keyboard actions. These controls capture key events with KeyboardListener and
render the value as plain Text, so the workflow stays in the current ELN page.
"""

from __future__ import annotations

from typing import Callable, Optional

import flet as ft


class WebKeyboardInput(ft.Column):
    """A TextField-like editor for Flet Web that does not use TextField."""

    def __init__(
        self,
        value: str = "",
        on_change: Optional[Callable[[str], None]] = None,
        on_submit: Optional[Callable[[str], None]] = None,
        placeholder: str = "点击后输入",
        width: int | None = None,
        multiline: bool = False,
    ):
        super().__init__(spacing=4)
        self.value = value or ""
        self.on_change = on_change
        self.on_submit = on_submit
        self.placeholder = placeholder
        self.multiline = multiline
        self._editing = False

        self._value_text = ft.Text(
            self._display_value(),
            size=14,
            color=ft.Colors.GREY_900 if self.value else ft.Colors.GREY_500,
            selectable=False,
            expand=True,
        )
        self._status = ft.Text("", size=11, color=ft.Colors.GREY_500)
        self._box = ft.Container(
            content=ft.Row([
                self._value_text,
                ft.Text("│", color=ft.Colors.ORANGE_600, visible=False),
            ], spacing=2),
            border=ft.Border.all(1, ft.Colors.ORANGE_300),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            bgcolor=ft.Colors.WHITE,
            width=width,
            ink=True,
            on_click=self._start_editing,
        )
        self._cursor = self._box.content.controls[1]
        self.controls = [self._box, self._status]

    def focus(self) -> None:
        self._start_editing(None)

    def _start_editing(self, _) -> None:
        self._editing = True
        self._cursor.visible = True
        self._status.value = "正在编辑：输入文字，Enter 保存，Esc 取消编辑"
        self._status.color = ft.Colors.ORANGE_700
        self._install_page_keyboard_handler()
        self._refresh()

    def _finish_editing(self) -> None:
        self._editing = False
        self._cursor.visible = False
        self._status.value = "已记录，稍后会自动保存"
        self._status.color = ft.Colors.GREEN_700
        if self.on_submit:
            self.on_submit(self.value)
        self._refresh()

    def _cancel_editing(self) -> None:
        self._editing = False
        self._cursor.visible = False
        self._status.value = ""
        self._refresh()

    def _install_page_keyboard_handler(self) -> None:
        page = getattr(self, "page", None)
        if page is None:
            return
        setattr(page, "_eln_keyboard_target", self)
        if getattr(page, "_eln_keyboard_handler_installed", False):
            return
        previous_handler = getattr(page, "on_keyboard_event", None)
        setattr(page, "_eln_previous_keyboard_handler", previous_handler)

        def _dispatch(e) -> None:
            target = getattr(page, "_eln_keyboard_target", None)
            if target is not None:
                target.handle_key_event(e)
                return
            prev = getattr(page, "_eln_previous_keyboard_handler", None)
            if prev:
                prev(e)

        page.on_keyboard_event = _dispatch
        setattr(page, "_eln_keyboard_handler_installed", True)

    def handle_key_event(self, e) -> None:
        if not self._editing:
            self._start_editing(None)

        key = getattr(e, "key", "") or ""
        if key in ("Enter", "Numpad Enter"):
            if self.multiline:
                self._append("\n")
            else:
                self._finish_editing()
            return
        if key == "Escape":
            self._cancel_editing()
            return
        if key == "Backspace":
            self.value = self.value[:-1]
            self._changed()
            return
        if key == "Delete":
            self.value = ""
            self._changed()
            return
        if key in ("Space", " "):
            self._append(" ")
            return

        char = _key_to_char(key, shift=getattr(e, "shift", False))
        if char:
            self._append(char)

    def _append(self, text: str) -> None:
        self.value += text
        self._changed()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(self.value)
        self._refresh()

    def _refresh(self) -> None:
        self._value_text.value = self._display_value()
        self._value_text.color = ft.Colors.GREY_900 if self.value else ft.Colors.GREY_500
        try:
            self.update()
        except Exception:
            pass

    def _display_value(self) -> str:
        return self.value if self.value else self.placeholder


def _key_to_char(key: str, shift: bool = False) -> str:
    if len(key) == 1:
        return key
    shifted_digits = {
        "1": "!",
        "2": "@",
        "3": "#",
        "4": "$",
        "5": "%",
        "6": "^",
        "7": "&",
        "8": "*",
        "9": "(",
        "0": ")",
    }
    named = {
        "Minus": "-",
        "Equal": "=",
        "Comma": ",",
        "Period": ".",
        "Slash": "/",
        "Backslash": "\\",
        "Semicolon": ";",
        "Quote": "'",
        "Bracket Left": "[",
        "Bracket Right": "]",
        "Backquote": "`",
        "Numpad Add": "+",
        "Numpad Subtract": "-",
        "Numpad Multiply": "*",
        "Numpad Divide": "/",
        "Numpad Decimal": ".",
    }
    if key in named:
        return named[key]
    if key.startswith("Digit "):
        digit = key.split(" ", 1)[1]
        return shifted_digits.get(digit, digit) if shift else digit
    if key.startswith("Numpad ") and key[-1:].isdigit():
        return key[-1]
    return ""
