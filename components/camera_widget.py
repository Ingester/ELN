"""
ELN App — CameraWidget
Handles photo capture or file pick, upload to server, and skip option.
On iOS: uses FilePicker (camera source).
On desktop: uses FilePicker (file browse).
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




class CameraWidget(ft.Column):
    """
    step_id: which step this photo belongs to
    experiment_id: for API routing
    existing_paths: already-uploaded photo paths
    camera_required: if True, skip button is hidden
    on_photo_added: called with rel_path when a photo is uploaded
    on_skip: called when user skips photo
    is_mobile: affects layout
    data_provider: object with upload_photo(step_id, file_path) → {path, url}
                   and photo_url(rel_path) → str
    """

    def __init__(
        self,
        step_id: int,
        experiment_id: int,
        existing_paths: list[str],
        camera_required: bool,
        on_photo_added: Optional[Callable[[str], None]] = None,
        on_skip: Optional[Callable[[], None]] = None,
        is_mobile: bool = True,
        data_provider=None,
    ):
        super().__init__(spacing=8)
        self.step_id = step_id
        self.experiment_id = experiment_id
        self.existing_paths = list(existing_paths)
        self.camera_required = camera_required
        self.on_photo_added = on_photo_added
        self.on_skip = on_skip
        self.is_mobile = is_mobile
        self.data_provider = data_provider
        self._uploading = False
        self._web_mode = os.environ.get("ELN_WEB_MODE") == "1"

        # Build sub-controls
        self._photo_list = ft.Column(spacing=6)
        self._status = ft.Text("", size=12, color=ft.Colors.GREY_600)
        self._progress = ft.ProgressRing(visible=False, width=20, height=20)
        self._file_picker = None if self._web_mode else ft.FilePicker()

        if self._web_mode:
            self._btn_photo = ft.ElevatedButton(
                "上传照片/文件",
                on_click=self._open_web_uploader,
                bgcolor=ft.Colors.ORANGE_600,
                color=ft.Colors.WHITE,
                tooltip="打开浏览器原生上传页面",
            )
            self._btn_fallback = ft.Container()
            self._status.value = "可在上传页面拍照、选相册或上传文件"
        else:
            self._btn_photo = ft.ElevatedButton(
                "上传照片/文件",
                on_click=self._pick_file,
                bgcolor=ft.Colors.ORANGE_600,
                color=ft.Colors.WHITE,
            )
            self._btn_fallback = ft.Container()
        self._btn_refresh = ft.TextButton(
            "刷新附件",
            on_click=self._refresh_from_provider,
            visible=self._web_mode,
        )
        self._btn_skip = ft.TextButton(
            "跳过拍照",
            on_click=self._on_skip_click,
            style=ft.ButtonStyle(color=ft.Colors.GREY_500),
            visible=not self.camera_required,
        )

        self._refresh_photo_list()

        self.controls = [
            self._photo_list,
            ft.Row([self._progress, self._status], spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([self._btn_photo, self._btn_fallback, self._btn_refresh, self._btn_skip], spacing=8),
        ]

    def did_mount(self) -> None:
        if self._file_picker is None:
            return
        services = getattr(self.page, "services", None)
        if services is not None and self._file_picker not in services:
            services.append(self._file_picker)
            self.page.update()
        elif self._file_picker not in self.page.overlay:
            self.page.overlay.append(self._file_picker)
            self.page.update()

    async def _pick_file(self, _) -> None:
        if self._file_picker is None:
            return
        files = await self._file_picker.pick_files(
            allow_multiple=False,
            file_type=ft.FilePickerFileType.ANY,
        )
        self._handle_picked_files(files)

    def _web_upload_url(self) -> str:
        return (
            f"{self._api_base_url()}/web/upload/{self.step_id}"
            f"?experiment_id={self.experiment_id}"
        )

    def _api_base_url(self) -> str:
        configured = "" if os.environ.get("ELN_DYNAMIC_PUBLIC_URL") == "1" else os.environ.get("ELN_API_PUBLIC_URL", "").rstrip("/")
        if configured:
            return configured
        try:
            from server.startup import get_local_ip
            return f"http://{get_local_ip()}:8000"
        except Exception:
            return "http://127.0.0.1:8000"

    def _open_web_uploader(self, _) -> None:
        url = self._web_upload_url()
        try:
            self.page.launch_url(url, web_popup_window_name=ft.UrlTarget.SELF)
            self._status.value = "上传完成后回到这里点「刷新附件」"
        except Exception:
            self._status.value = f"请在浏览器打开：{url}"
        self.update()

    def _refresh_from_provider(self, _=None) -> None:
        if not self.data_provider:
            return
        try:
            step = self.data_provider.get_step(self.step_id)
            paths = step.get_photo_paths() if hasattr(step, "get_photo_paths") else step.get("photo_paths", [])
            self.existing_paths = list(paths or [])
            self._refresh_photo_list()
            self._status.value = f"已刷新，当前 {len(self.existing_paths)} 张照片"
            if self.existing_paths and self.on_photo_added:
                self.on_photo_added(self.existing_paths[-1])
        except Exception as ex:
            self._status.value = f"刷新失败：{ex}"
        self.update()

    def _handle_picked_files(self, files) -> None:
        if not files:
            return
        file = files[0]
        file_path = file.path
        file_bytes = getattr(file, "bytes", None)
        if not file_path and not file_bytes:
            self._status.value = "无法获取文件路径"
            self.update()
            return

        self._uploading = True
        self._progress.visible = True
        self._status.value = "上传中…"
        self.update()

        try:
            if self.data_provider and file_bytes:
                result = self.data_provider.upload_photo_bytes(
                    self.step_id,
                    getattr(file, "name", "photo.jpg"),
                    file_bytes,
                )
                rel_path = result.get("path", "")
            elif self.data_provider:
                result = self.data_provider.upload_photo(self.step_id, file_path)
                rel_path = result.get("path", "")
            else:
                rel_path = file_path

            self.existing_paths.append(rel_path)
            self._status.value = "上传成功"
            self._refresh_photo_list()
            if self.on_photo_added:
                self.on_photo_added(rel_path)
        except Exception as ex:
            self._status.value = f"上传失败：{ex}"
        finally:
            self._uploading = False
            self._progress.visible = False
            self.update()

    def _on_skip_click(self, _) -> None:
        if self.on_skip:
            self.on_skip()
        self._status.value = "已跳过拍照（可在实验结束后补充）"
        self._btn_photo.visible = False
        self._btn_skip.visible = False
        self.update()

    def _refresh_photo_list(self) -> None:
        self._photo_list.controls.clear()
        for i, path in enumerate(self.existing_paths, 1):
            if self._web_mode:
                url = f"{self._api_base_url()}/photos/{path}"
            elif self.data_provider:
                url = self.data_provider.photo_url(path)
            else:
                url = path
            self._photo_list.controls.append(
                ft.Row([
                    ft.Icon(ft.Icons.ATTACH_FILE, color=ft.Colors.ORANGE_400),
                    ft.Text(f"附件 {i}", size=13),
                    ft.TextButton(
                        "查看",
                        on_click=lambda _, u=url: self._view_photo(u),
                        style=ft.ButtonStyle(color=ft.Colors.ORANGE_600),
                    ),
                ], spacing=6)
            )

    def _view_photo(self, url: str) -> None:
        self.page.launch_url(url)

    def _close_dlg(self, dlg) -> None:
        dlg.open = False
        self.page.update()
