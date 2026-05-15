"""
ELN App — BoxGrid
Interactive grid component for a Box (9×9 or 10×10).
Slots are coloured by occupancy.
Supports selection mode (for checkin) and view mode (for management).
"""

from __future__ import annotations
import flet as ft
from typing import Callable, Optional

from db.models import Box, BoxSlot

# Row labels A-J (10 rows max)
_ROW_LABELS = list("ABCDEFGHIJ")

_C_EMPTY    = ft.Colors.GREY_100
_C_OCCUPIED = ft.Colors.ORANGE_200
_C_SELECTED = ft.Colors.ORANGE_600
_C_HEADER   = ft.Colors.GREY_300


class BoxGrid(ft.Column):
    """
    box: Box model
    slots: list of BoxSlot (occupied positions)
    mode: "view" | "select"
      - view: clicking a slot shows info dialog
      - select: clicking an empty slot selects it; calls on_select(row, col)
    selected_position: pre-selected position string (e.g. "A1") in select mode
    on_select: called with (row_label, col_label) when user picks a slot
    on_slot_click: called with BoxSlot when user clicks occupied slot in view mode
    """

    def __init__(
        self,
        box: Box,
        slots: list[BoxSlot],
        mode: str = "view",
        selected_position: Optional[str] = None,
        on_select: Optional[Callable[[str, str], None]] = None,
        on_slot_click: Optional[Callable[[BoxSlot], None]] = None,
    ):
        super().__init__(spacing=8)
        self.box = box
        self.slots = slots
        self.mode = mode
        self.selected_position = selected_position
        self.on_select = on_select
        self.on_slot_click = on_slot_click

        # Build lookup: "A1" → BoxSlot
        self._slot_map: dict[str, BoxSlot] = {
            f"{s.row_label}{s.col_label}": s for s in slots
        }

        self._build_controls()

    def _build_controls(self) -> None:
        size = self.box.box_size  # 9 or 10
        rows_used = _ROW_LABELS[:size]
        cols_used = [str(i) for i in range(1, size + 1)]

        cell_size = 34 if size == 10 else 38

        # Header row: empty corner + column numbers
        header_cells = [ft.Container(width=cell_size, height=cell_size)]
        for col in cols_used:
            header_cells.append(ft.Container(
                content=ft.Text(col, size=10, text_align=ft.TextAlign.CENTER,
                                color=ft.Colors.GREY_600),
                width=cell_size, height=cell_size,
                alignment=ft.Alignment.CENTER,
            ))

        grid_rows: list[ft.Row] = [ft.Row(header_cells, spacing=2)]

        for row in rows_used:
            row_cells = [
                ft.Container(
                    content=ft.Text(row, size=10, text_align=ft.TextAlign.CENTER,
                                    color=ft.Colors.GREY_600),
                    width=cell_size, height=cell_size,
                    alignment=ft.Alignment.CENTER,
                )
            ]
            for col in cols_used:
                pos = f"{row}{col}"
                slot = self._slot_map.get(pos)
                is_selected = (self.selected_position == pos)
                cell = self._build_cell(row, col, slot, is_selected, cell_size)
                row_cells.append(cell)
            grid_rows.append(ft.Row(row_cells, spacing=2))

        self.controls = [
            ft.Text(
                f"{self.box.box_name}  ({size}×{size})",
                size=14, weight=ft.FontWeight.BOLD,
            ),
            ft.Column(grid_rows, spacing=2),
            self._build_legend(),
        ]

    def _build_cell(self, row: str, col: str, slot: Optional[BoxSlot],
                    is_selected: bool, size: int) -> ft.Container:
        pos = f"{row}{col}"

        if is_selected:
            bg = _C_SELECTED
            tooltip = f"{pos} (已选)"
            text_color = ft.Colors.WHITE
        elif slot:
            bg = _C_OCCUPIED
            tooltip = f"{pos}: {slot.sample_name}"
            text_color = ft.Colors.BROWN_800
        else:
            bg = _C_EMPTY
            tooltip = pos
            text_color = ft.Colors.GREY_400

        label = slot.sample_name[:2] if slot and slot.sample_name else ""

        def _on_click(_, r=row, c=col, s=slot):
            if self.mode == "select":
                self.selected_position = f"{r}{c}" if not is_selected else None
                if self.on_select and not is_selected:
                    self.on_select(r, c)
                self._rebuild()
            else:
                if self.on_slot_click:
                    if s is None:
                        s = BoxSlot(
                            id=0,
                            box_id=self.box.id,
                            row_label=r,
                            col_label=c,
                            sample_name="",
                            notes="",
                            experiment_id=None,
                            step_id=None,
                            created_at="",
                        )
                    self.on_slot_click(s)

        return ft.Container(
            content=ft.Text(label, size=9, text_align=ft.TextAlign.CENTER,
                            color=text_color),
            width=size, height=size,
            bgcolor=bg,
            border_radius=3,
            border=ft.Border.all(1, ft.Colors.GREY_300),
            alignment=ft.Alignment.CENTER,
            tooltip=tooltip,
            on_click=_on_click,
        )

    def _build_legend(self) -> ft.Row:
        def _dot(color, label):
            return ft.Row([
                ft.Container(width=12, height=12, bgcolor=color, border_radius=2),
                ft.Text(label, size=11, color=ft.Colors.GREY_600),
            ], spacing=4)

        items = [_dot(_C_EMPTY, "空"), _dot(_C_OCCUPIED, "已占用")]
        if self.mode == "select":
            items.append(_dot(_C_SELECTED, "已选"))
        return ft.Row(items, spacing=12)

    def _rebuild(self) -> None:
        self._build_controls()
        try:
            self.update()
        except Exception:
            pass

    def update_slots(self, slots: list[BoxSlot]) -> None:
        """Refresh slot data and redraw."""
        self.slots = slots
        self._slot_map = {f"{s.row_label}{s.col_label}": s for s in slots}
        self._rebuild()
