"""
ELN App — Report View
Displays the Markdown experiment report.
Supports copy-to-clipboard and mark experiment as completed.
"""

from __future__ import annotations
import os
import flet as ft
from typing import Callable, Optional

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




def build_report_view(
    page: ft.Page,
    data_provider,
    experiment_id: int,
    on_back: Callable[[], None],
    is_mobile: bool = True,
) -> ft.Control:

    _md_content: list[str] = [""]
    _experiment_status: list[str] = [""]
    _saved_path: list[str] = [""]
    saved_path_text = ft.Text("", size=12, color=ft.Colors.GREY_600, selectable=True)

    def _photo_src(path: str) -> str:
        if os.environ.get("ELN_WEB_MODE") == "1":
            return f"{_api_base_url()}/photos/{path}"
        if hasattr(data_provider, "photo_url"):
            return data_provider.photo_url(path)
        return path

    def _api_base_url() -> str:
        configured = "" if os.environ.get("ELN_DYNAMIC_PUBLIC_URL") == "1" else os.environ.get("ELN_API_PUBLIC_URL", "").rstrip("/")
        if configured:
            return configured
        try:
            from server.startup import get_local_ip
            return f"http://{get_local_ip()}:8000"
        except Exception:
            return "http://127.0.0.1:8000"

    def _photo_paths_from_step(step) -> list[str]:
        if isinstance(step, dict):
            paths = step.get("photo_paths", [])
            if isinstance(paths, str):
                try:
                    import json
                    paths = json.loads(paths or "[]")
                except Exception:
                    paths = []
            return [str(p) for p in paths if p]
        return [str(p) for p in step.get_photo_paths() if p]

    def _step_label(step) -> str:
        if isinstance(step, dict):
            return f"Step {int(step.get('step_index', 0)) + 1} · {step.get('title', '')}"
        return f"Step {step.step_index + 1} · {step.title}"

    def _build_photo_gallery() -> ft.Control:
        rows: list[ft.Control] = []
        for step in data_provider.get_steps(experiment_id):
            paths = _photo_paths_from_step(step)
            if not paths:
                continue
            thumbs = []
            for i, path in enumerate(paths, 1):
                src = _photo_src(path)
                thumbs.append(ft.Container(
                    content=ft.Column([
                        ft.Image(src=src, fit=ft.BoxFit.COVER, width=180, height=120),
                        ft.Text(f"照片 {i}", size=12, color=ft.Colors.GREY_700),
                        ft.Text(path, size=11, color=ft.Colors.GREY_500, selectable=True),
                    ], spacing=4),
                    border=ft.Border.all(1, ft.Colors.GREY_200),
                    border_radius=8,
                    padding=8,
                    width=200,
                ))
            rows.append(ft.Container(
                content=ft.Column([
                    ft.Text(_step_label(step), size=14, weight=ft.FontWeight.BOLD),
                    ft.Row(thumbs, wrap=True, spacing=10, run_spacing=10),
                ], spacing=8),
                padding=ft.Padding.symmetric(vertical=8),
            ))
        if not rows:
            return ft.Container()
        return ft.Container(
            content=ft.Column([
                ft.Divider(),
                ft.Text("照片预览", size=18, weight=ft.FontWeight.BOLD),
                *rows,
            ], spacing=8),
        )

    def _load():
        try:
            exp = data_provider.get_experiment(experiment_id)
            _experiment_status[0] = exp.get("status", "") if isinstance(exp, dict) else getattr(exp, "status", "")
            result = data_provider.get_report(experiment_id)
            if isinstance(result, dict):
                md = result.get("markdown", "")
            else:
                md = result
            _md_content[0] = md
            report_container.content = ft.Column(
                [
                    ft.Markdown(
                        value=md,
                        selectable=True,
                        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                        code_theme="atom-one-light",
                    ),
                    _build_photo_gallery(),
                ],
                spacing=16,
            )
        except Exception as e:
            report_container.content = ft.Text(
                f"报告生成失败：{e}", color=ft.Colors.RED_400
            )
        try:
            page.update()
        except Exception:
            pass

    report_container = ft.Container(
        content=ft.Text("加载中…", color=ft.Colors.GREY_500),
        expand=True,
    )

    def _copy_report(_):
        if _md_content[0]:
            page.set_clipboard(_md_content[0])
            _open_overlay(page, ft.SnackBar(                content=ft.Text("报告已复制到剪贴板")            ))

    def _save_report(_):
        try:
            if hasattr(data_provider, "save_report"):
                result = data_provider.save_report(experiment_id)
                path = result.get("path", "") if isinstance(result, dict) else str(result)
            else:
                raise RuntimeError("当前数据后端还没有保存报告接口")
            _saved_path[0] = path
            saved_path_text.value = f"已保存：{path}"
            saved_path_text.color = ft.Colors.GREEN_700
            _open_overlay(page, ft.SnackBar(
                content=ft.Text(f"报告已保存：{path}"),
                bgcolor=ft.Colors.GREEN_600,
            ))
        except Exception as ex:
            saved_path_text.value = f"保存失败：{ex}"
            saved_path_text.color = ft.Colors.RED_400
            _open_overlay(page, ft.SnackBar(
                content=ft.Text(f"保存失败：{ex}"),
                bgcolor=ft.Colors.RED_400,
            ))
        try:
            page.update()
        except Exception:
            pass

    def _mark_complete(_):
        try:
            storage_items = data_provider.get_storage_items(experiment_id)
            pending_storage = [
                item for item in storage_items
                if not (item.get("is_registered", False) if isinstance(item, dict) else item.is_registered)
            ]
            pending_photos = data_provider.get_pending_photos(experiment_id)
            if pending_storage or pending_photos:
                msg_parts = []
                if pending_storage:
                    msg_parts.append(f"{len(pending_storage)} 个样品未登记")
                if pending_photos:
                    msg_parts.append(f"{len(pending_photos)} 个拍照步骤待补")
                _open_overlay(page, ft.SnackBar(
                    content=ft.Text("收尾未完成：" + "，".join(msg_parts)),
                    bgcolor=ft.Colors.ORANGE_600,
                ))
                return
            data_provider.update_experiment(experiment_id, status="completed")
            _open_overlay(page, ft.SnackBar(                content=ft.Text("实验已标记为完成"),                bgcolor=ft.Colors.GREEN_600,            ))
        except Exception as ex:
            _open_overlay(page, ft.SnackBar(                content=ft.Text(f"操作失败：{ex}"),                bgcolor=ft.Colors.RED_400,            ))

    header = ft.Row([
        ft.IconButton(ft.Icons.ARROW_BACK,
                      on_click=lambda _: on_back(),
                      tooltip="返回"),
        ft.Text("实验报告", size=18, weight=ft.FontWeight.BOLD),
        ft.Container(expand=True),
        ft.IconButton(
            ft.Icons.COPY,
            tooltip="复制 Markdown",
            on_click=_copy_report,
            icon_color=ft.Colors.GREY_600,
        ),
        ft.ElevatedButton(
            "保存报告",
            icon=ft.Icons.SAVE_ALT,
            on_click=_save_report,
            bgcolor=ft.Colors.ORANGE_600,
            color=ft.Colors.WHITE,
            height=32,
        ),
        ft.ElevatedButton(
            "标记完成",
            on_click=_mark_complete,
            bgcolor=ft.Colors.GREEN_600,
            color=ft.Colors.WHITE,
            height=32,
        ),
    ])

    _load()

    return ft.Column([
        ft.Container(
            content=header,
            padding=ft.Padding.symmetric(horizontal=8, vertical=8),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.GREY_200)),
        ),
        ft.Container(
            content=ft.Column(
                [saved_path_text, report_container],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            expand=True,
        ),
    ], expand=True, spacing=0)
