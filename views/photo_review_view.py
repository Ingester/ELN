"""
ELN App — Photo Review View
Shows all steps where photo was skipped (photo_pending=True).
User can upload photos here before finalizing the experiment.
"""

from __future__ import annotations
import flet as ft
from typing import Callable

from components.camera_widget import CameraWidget


def build_photo_review_view(
    page: ft.Page,
    data_provider,
    experiment_id: int,
    on_done: Callable[[], None],
    is_mobile: bool = True,
) -> ft.Control:

    steps_col = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

    def _load():
        steps_col.controls.clear()
        try:
            pending = data_provider.get_pending_photos(experiment_id)
        except Exception as e:
            steps_col.controls.append(
                ft.Text(f"加载失败：{e}", color=ft.Colors.RED_400)
            )
            page.update()
            return

        if not pending:
            steps_col.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE,
                                size=64, color=ft.Colors.GREEN_400),
                        ft.Text("所有照片已完成", size=16,
                                color=ft.Colors.GREY_500,
                                text_align=ft.TextAlign.CENTER),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12),
                    alignment=ft.Alignment.CENTER, padding=40,
                )
            )
        else:
            for step_data in pending:
                steps_col.controls.append(_build_step_photo_card(step_data))
        page.update()

    def _build_step_photo_card(step_data) -> ft.Container:
        # step_data may be dict (API) or Step object
        if isinstance(step_data, dict):
            step_id = step_data["id"]
            step_idx = step_data["step_index"]
            title = step_data["title"]
            existing = step_data.get("attachments") or step_data.get("photo_paths", [])
            if isinstance(existing, str):
                import json
                existing = json.loads(existing)
        else:
            step_id = step_data.id
            step_idx = step_data.step_index
            title = step_data.title
            existing = step_data.get_attachments()

        def _on_photo_added(rel_path: str):
            _load()  # Refresh list

        camera = CameraWidget(
            step_id=step_id,
            experiment_id=experiment_id,
            existing_paths=existing,
            camera_required=False,
            on_photo_added=_on_photo_added,
            on_skip=None,
            is_mobile=is_mobile,
            data_provider=data_provider,
        )

        return ft.Container(
            content=ft.Column([
                ft.Text(f"Step {step_idx + 1} · {title}",
                        size=14, weight=ft.FontWeight.W_500),
                camera,
            ], spacing=8),
            border=ft.Border.all(1, ft.Colors.ORANGE_200),
            border_radius=8,
            padding=12,
            bgcolor=ft.Colors.ORANGE_50,
        )

    _load()

    header = ft.Row([
        ft.Text("补充照片", size=18, weight=ft.FontWeight.BOLD),
        ft.Container(expand=True),
        ft.ElevatedButton(
            "完成",
            on_click=lambda _: on_done(),
            bgcolor=ft.Colors.ORANGE_600,
            color=ft.Colors.WHITE,
        ),
    ])

    return ft.Column([
        ft.Container(
            content=header,
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.GREY_200)),
        ),
        ft.Container(content=steps_col, padding=12, expand=True),
    ], expand=True, spacing=0)
