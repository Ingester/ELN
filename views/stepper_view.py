"""
ELN App — Stepper View
Full-screen experiment execution view.
Hosts StepCard components with swipe/button navigation.
Handles experiment completion flow:
  1. Photo review (pending photos)
  2. Box storage checkin
  3. Report generation
"""

from __future__ import annotations
import flet as ft
from typing import Callable, Optional

from db.models import Step, Experiment
from components.step_card import StepCard

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




class StepperView(ft.Column):
    """
    experiment_id: which experiment to run
    on_back: navigate back to home
    on_complete: called when experiment is fully completed
    is_mobile: layout mode
    data_provider: db.database or utils.api_client
    """

    def __init__(
        self,
        experiment_id: int,
        on_back: Optional[Callable[[], None]] = None,
        on_complete: Optional[Callable[[int], None]] = None,
        is_mobile: bool = True,
        data_provider=None,
        navigate_to: Optional[Callable[[str, dict], None]] = None,
        focus_step_id: Optional[int] = None,
    ):
        super().__init__(expand=True, spacing=0)
        self.experiment_id = experiment_id
        self.on_back = on_back
        self.on_complete = on_complete
        self.is_mobile = is_mobile
        self.data_provider = data_provider
        self.navigate_to = navigate_to
        self.focus_step_id = focus_step_id

        self._steps: list[Step] = []
        self._current_index: int = 0
        self._experiment: Optional[Experiment] = None
        self._current_card: Optional[StepCard] = None

        self._load_data()
        self._build_controls()

    def _build_controls(self) -> None:
        if not self._steps:
            self.controls = [
                ft.Container(
                    content=ft.Text("无步骤数据", color=ft.Colors.GREY_500),
                    alignment=ft.Alignment.CENTER,
                    expand=True,
                )
            ]
            return

        # Top app bar
        self._title_button = ft.TextButton(
            self._experiment.name if self._experiment else "实验",
            on_click=self._open_experiment_title_editor,
            style=ft.ButtonStyle(
                color=ft.Colors.BLACK,
                padding=ft.Padding.symmetric(horizontal=6, vertical=4),
            ),
        )
        app_bar = ft.Container(
            content=ft.Row([
                ft.IconButton(
                    ft.Icons.ARROW_BACK,
                    on_click=self._on_back_click,
                    tooltip="返回",
                ),
                self._title_button,
                ft.Container(expand=True),
                ft.IconButton(
                    ft.Icons.REFRESH,
                    tooltip="刷新当前步骤",
                    on_click=self._on_refresh_current,
                ),
                ft.IconButton(
                    ft.Icons.SUMMARIZE_OUTLINED,
                    tooltip="查看报告",
                    on_click=self._on_view_report,
                ),
            ]),
            padding=ft.Padding.symmetric(horizontal=4, vertical=4),
            bgcolor=ft.Colors.WHITE,
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.GREY_200)),
        )

        # Step card area
        self._card_container = ft.Container(expand=True)
        self._render_current_step()

        self.controls = [
            app_bar,
            self._card_container,
        ]

    # ── Data loading ────────────────────────────

    def _load_data(self) -> None:
        try:
            exp_data = self.data_provider.get_experiment(self.experiment_id)
            if isinstance(exp_data, dict):
                from db.models import Experiment
                self._experiment = Experiment(
                    id=exp_data["id"], name=exp_data["name"],
                    created_at=exp_data["created_at"], status=exp_data["status"],
                    protocol_json=exp_data.get("protocol_json", "{}"),
                    protocol_id=exp_data.get("protocol_id"),
                    notes=exp_data.get("notes", ""),
                )
            else:
                self._experiment = exp_data

            steps_data = self.data_provider.get_steps(self.experiment_id)
            if steps_data and isinstance(steps_data[0], dict):
                self._steps = [_dict_to_step(s) for s in steps_data]
            else:
                self._steps = steps_data

            # Find first incomplete step
            if self.focus_step_id is not None:
                for i, s in enumerate(self._steps):
                    if s.id == self.focus_step_id:
                        self._current_index = i
                        break
                else:
                    self._set_first_incomplete_step()
            else:
                self._set_first_incomplete_step()

        except Exception as e:
            self._steps = []
            print(f"StepperView load error: {e}")

    def _set_first_incomplete_step(self) -> None:
        for i, s in enumerate(self._steps):
            if not s.completed_at:
                self._current_index = i
                break
        else:
            self._current_index = len(self._steps) - 1

    # ── Step rendering ──────────────────────────

    def _render_current_step(self) -> None:
        if not self._steps:
            return
        try:
            step = self._steps[self._current_index]
            card = StepCard(
                step=step,
                total_steps=len(self._steps),
                on_complete=self._on_step_complete,
                on_prev=self._go_prev if self._current_index > 0 else None,
                on_next=self._go_next if self._current_index < len(self._steps) - 1 else None,
                on_add_storage=self._open_add_storage_dialog if self._current_index == len(self._steps) - 1 else None,
                on_checkin_storage=self._go_storage_checkin if self._current_index == len(self._steps) - 1 else None,
                is_mobile=self.is_mobile,
                data_provider=self.data_provider,
            )
            self._current_card = card
            self._card_container.content = card
        except Exception as ex:
            self._current_card = None
            self._card_container.content = ft.Container(
                content=ft.Column([
                    ft.Text("步骤页面加载失败", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700),
                    ft.Text(str(ex), color=ft.Colors.RED_600),
                    ft.ElevatedButton("返回首页", on_click=lambda _: self.on_back() if self.on_back else None),
                ], spacing=12),
                padding=24,
            )
        try:
            self.update()
        except Exception:
            pass

    def _open_experiment_title_editor(self, _) -> None:
        tf = ft.TextField(
            label="实验标题",
            value=self._experiment.name if self._experiment else "",
            width=420,
            autofocus=True,
        )
        err = ft.Text("", size=12, color=ft.Colors.RED_600)

        def _save(_):
            name = (tf.value or "").strip()
            if not name:
                err.value = "标题不能为空"
                self.page.update()
                return
            try:
                self.data_provider.update_experiment(self.experiment_id, name=name)
                if self._experiment:
                    self._experiment.name = name
                self._title_button.text = name
                _close_overlay(self.page, dlg)
                self.page.update()
            except Exception as ex:
                err.value = f"保存失败：{ex}"
                self.page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("编辑实验标题"),
            content=ft.Column([tf, err], tight=True, spacing=8),
            actions=[
                ft.TextButton("取消", on_click=lambda _: _close_overlay(self.page, dlg)),
                ft.ElevatedButton("保存", on_click=_save),
            ],
        )
        _open_overlay(self.page, dlg)

    def _go_storage_checkin(self) -> None:
        self._persist_current_card()
        if self.navigate_to:
            self.navigate_to("box_checkin", {"experiment_id": self.experiment_id})

    def _on_refresh_current(self, _) -> None:
        current_step_id = self._steps[self._current_index].id if self._steps else None
        self._load_data()
        if current_step_id is not None:
            for i, s in enumerate(self._steps):
                if s.id == current_step_id:
                    self._current_index = i
                    break
        self._render_current_step()

    def _go_prev(self) -> None:
        if self._current_index > 0:
            self._persist_current_card()
            self._current_index -= 1
            self._render_current_step()

    def _go_next(self) -> None:
        if self._current_index < len(self._steps) - 1:
            self._persist_current_card()
            self._current_index += 1
            self._render_current_step()

    def _on_step_complete(self, updated_data: dict) -> None:
        """Called when a step is marked complete."""
        try:
            steps_data = self.data_provider.get_steps(self.experiment_id)
            if steps_data and isinstance(steps_data[0], dict):
                self._steps = [_dict_to_step(s) for s in steps_data]
            else:
                self._steps = steps_data
        except Exception:
            pass

        all_done = all(s.completed_at for s in self._steps)
        if all_done:
            self._on_all_steps_complete()
        else:
            for i, s in enumerate(self._steps):
                if not s.completed_at:
                    self._current_index = i
                    break
            self._render_current_step()

    def _on_all_steps_complete(self) -> None:
        """All steps done — start completion flow."""
        try:
            self.data_provider.update_experiment(self.experiment_id, status="needs_wrapup")
        except Exception:
            pass
        try:
            pending = self.data_provider.get_pending_photos(self.experiment_id)
        except Exception:
            pending = []

        self._show_completion_dialog(has_pending_photos=bool(pending))

    def _show_completion_dialog(self, has_pending_photos: bool) -> None:
        pending_note = (
            "\n⚠️ 有跳过的拍照步骤，建议先补充照片。"
            if has_pending_photos else ""
        )
        storage_note = "\n\n如有物品需要储存，请在最后一步页面的「储存物品」区域添加并登记位置。"

        def _go_report(_):
            dlg.open = False
            self.page.update()
            self._on_view_report(None)

        def _go_photos(_):
            dlg.open = False
            self.page.update()
            if self.navigate_to:
                self.navigate_to("photo_review", {"experiment_id": self.experiment_id})

        actions = []
        if has_pending_photos:
            actions.append(ft.TextButton("补充照片", on_click=_go_photos))
        actions.append(ft.ElevatedButton("查看报告", on_click=_go_report,
                                          bgcolor=ft.Colors.ORANGE_600,
                                          color=ft.Colors.WHITE))

        dlg = ft.AlertDialog(
            title=ft.Text("🎉 实验完成！"),
            content=ft.Text(
                f"所有步骤已完成。{pending_note}{storage_note}"
            ),
            actions=actions,
        )
        _open_overlay(self.page, dlg)
        dlg.open = True
        self.page.update()

    def _open_add_storage_dialog(self) -> None:
        help_text = (
            "一行一个物品。格式：名称 | 管型 | 备注\n"
            "也可以只写名称，例如：PCR 产物"
        )
        tf = ft.TextField(
            label="这次要储存的物品",
            hint_text="PCR 产物 | 1.5mL EP管 | sample A\n菌液甘油管 | 冻存管",
            multiline=True,
            min_lines=5,
            max_lines=8,
            width=520,
        )
        err = ft.Text("", size=12, color=ft.Colors.RED_600)

        def _parse_lines():
            parsed = []
            for line in (tf.value or "").splitlines():
                raw = line.strip()
                if not raw:
                    continue
                parts = [p.strip() for p in raw.split("|")]
                label = parts[0] if parts else ""
                if not label:
                    continue
                parsed.append({
                    "item_label": label,
                    "tube_type": parts[1] if len(parts) > 1 else "",
                    "notes_template": parts[2] if len(parts) > 2 else "",
                })
            return parsed

        def _save(_):
            items = _parse_lines()
            if not items:
                err.value = "请至少输入一个要储存的物品"
                self.page.update()
                return
            try:
                for item in items:
                    self.data_provider.create_storage_item(
                        self.experiment_id,
                        item_label=item["item_label"],
                        tube_type=item["tube_type"],
                        notes_template=item["notes_template"],
                    )
                _close_overlay(self.page, dlg)
                if self.navigate_to:
                    self.navigate_to("box_checkin", {"experiment_id": self.experiment_id})
            except Exception as ex:
                err.value = f"添加失败：{ex}"
                self.page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("添加储存物品"),
            content=ft.Column([
                ft.Text(help_text, size=12, color=ft.Colors.GREY_600),
                tf,
                err,
            ], tight=True, spacing=8),
            actions=[
                ft.TextButton("取消", on_click=lambda _: _close_overlay(self.page, dlg)),
                ft.ElevatedButton("添加并选位置", on_click=_save,
                                  bgcolor=ft.Colors.ORANGE_600,
                                  color=ft.Colors.WHITE),
            ],
        )
        _open_overlay(self.page, dlg)

    def _on_view_report(self, _) -> None:
        self._persist_current_card()
        if self.navigate_to:
            self.navigate_to("report", {"experiment_id": self.experiment_id})

    def _on_back_click(self, _) -> None:
        self._persist_current_card()
        if self.on_back:
            self.on_back()

    def _persist_current_card(self) -> None:
        try:
            if self._current_card is not None:
                self._current_card.persist_draft()
        except Exception:
            pass


# ─────────────────────────────────────────────
# Helper: dict → Step (for API mode)
# ─────────────────────────────────────────────

def _dict_to_step(d: dict) -> Step:
    import json as _json
    return Step(
        id=d["id"],
        experiment_id=d["experiment_id"],
        step_index=d["step_index"],
        title=d["title"],
        description=d["description"],
        timer_seconds=d.get("timer_seconds", 0),
        timer_override_seconds=d.get("timer_override_seconds"),
        timer_finished_at=d.get("timer_finished_at"),
        overtime_seconds=d.get("overtime_seconds", 0),
        has_camera=bool(d.get("has_camera", False)),
        camera_required=bool(d.get("camera_required", False)),
        fields_json=_json.dumps(d.get("fields", []), ensure_ascii=False)
                    if isinstance(d.get("fields"), list) else d.get("fields_json", "[]"),
        values_json=_json.dumps(d.get("values", {}), ensure_ascii=False)
                    if isinstance(d.get("values"), dict) else d.get("values_json", "{}"),
        description_overrides_json=_json.dumps(d.get("description_overrides", {}), ensure_ascii=False)
                                   if isinstance(d.get("description_overrides"), dict)
                                   else d.get("description_overrides_json", "{}"),
        photo_paths=_json.dumps(d.get("photo_paths", []), ensure_ascii=False)
                    if isinstance(d.get("photo_paths"), list) else d.get("photo_paths", "[]"),
        photo_pending=bool(d.get("photo_pending", False)),
        completed_at=d.get("completed_at"),
    )
