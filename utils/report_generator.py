"""
ELN App — Report Generator
Generates a Markdown experiment report from experiment + steps + storage data.
"""

from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Optional

from db.models import Experiment, Step, StorageItem, Box
from utils.inline_editor import render_plain

STEP_NOTES_KEY = "__eln_step_notes"


def generate_report(
    experiment: Experiment,
    steps: list[Step],
    storage_items: list[StorageItem],
    boxes: dict[int, Box],
    timer_events: list[dict] | None = None,
    voice_notes: list[dict] | None = None,
) -> str:
    """Return a complete Markdown report string."""
    lines: list[str] = []

    # ── Header ──────────────────────────────────
    lines.append(f"# 实验报告：{experiment.name}")
    lines.append("")
    lines.append(f"**创建时间**：{_fmt_dt(experiment.created_at)}")

    # Find last completed step time
    completed_times = [s.completed_at for s in steps if s.completed_at]
    if completed_times:
        lines.append(f"**完成时间**：{_fmt_dt(max(completed_times))}")

    lines.append(f"**状态**：{_status_label(experiment.status)}")
    if experiment.notes:
        lines.append(f"**备注**：{experiment.notes}")
    lines.append("")

    # ── Progress summary ────────────────────────
    total = len(steps)
    completed = sum(1 for s in steps if s.completed_at)
    lines.append(f"**步骤完成**：{completed} / {total}")
    lines.append("")

    # ── Step records ────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## 步骤记录")
    lines.append("")

    events_by_step: dict[int, list[dict]] = {}
    for event in timer_events or []:
        try:
            events_by_step.setdefault(int(event.get("step_id")), []).append(event)
        except Exception:
            pass

    notes_by_step: dict[int, list[dict]] = {}
    loose_notes: list[dict] = []
    for note in voice_notes or []:
        step_id = note.get("step_id")
        if step_id:
            notes_by_step.setdefault(int(step_id), []).append(note)
        else:
            loose_notes.append(note)

    for step in steps:
        lines.extend(_render_step(step, events_by_step.get(step.id, []),
                                  notes_by_step.get(step.id, [])))

    # ── Voice notes not attached to a step ───────
    if loose_notes:
        lines.append("---")
        lines.append("")
        lines.append("## 语音速记（未关联步骤）")
        lines.append("")
        lines.extend(_render_voice_notes(loose_notes))
        lines.append("")

    # ── Storage table ────────────────────────────
    if storage_items:
        lines.append("---")
        lines.append("")
        lines.append("## 存储登记")
        lines.append("")
        lines.extend(_render_storage_table(storage_items, boxes))
        lines.append("")

    # ── Pending photos ───────────────────────────
    pending_photo_steps = [s for s in steps if s.photo_pending]
    if pending_photo_steps:
        lines.append("---")
        lines.append("")
        lines.append("## 待补照片")
        lines.append("")
        lines.append("以下步骤在实验时跳过了拍照，请补充：")
        lines.append("")
        for s in pending_photo_steps:
            lines.append(f"- **Step {s.step_index + 1}** · {s.title}")
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Step rendering
# ─────────────────────────────────────────────

def _render_voice_notes(notes: list[dict]) -> list[str]:
    lines: list[str] = []
    for note in notes:
        stamp = _fmt_dt(note.get("created_at"))
        text = str(note.get("text") or "").strip()
        if text:
            lines.append(f"- 🎤 {stamp}：{text}")
        elif note.get("audio_path"):
            lines.append(f"- 🎤 {stamp}：录音待转写（`{note['audio_path']}`）")
    return lines


def _render_step(step: Step, timer_events: list[dict] | None = None,
                 voice_notes: list[dict] | None = None) -> list[str]:
    lines: list[str] = []
    status_icon = "✅" if step.completed_at else "⬜"
    lines.append(f"### {status_icon} Step {step.step_index + 1} · {step.title}")
    lines.append("")

    # Description with overrides applied
    overrides = step.get_description_overrides()
    if overrides:
        rendered_desc = render_plain(step.description, overrides)
        lines.append("**描述**：")
        lines.append("")
        lines.append(rendered_desc)
        lines.append("")
        lines.append("**参数修改**（相对原始协议）：")
        lines.append("")
        for key, new_val in overrides.items():
            lines.append(f"- `{key}` → `{new_val}` ⚠️ 已修改")
        lines.append("")
    else:
        lines.append("**描述**：")
        lines.append("")
        lines.append(step.description)
        lines.append("")

    # Field values
    fields = step.get_fields()
    values = step.get_values()
    if fields and values:
        lines.append("**记录值**：")
        lines.append("")
        for f in fields:
            val = values.get(f.key, "")
            default = f.default
            if val and str(val) != str(default):
                lines.append(f"- {f.label}：**{val}**（默认：{default}）⚠️ 已修改")
            elif val:
                lines.append(f"- {f.label}：{val}")
            else:
                lines.append(f"- {f.label}：*(未填写)*")
        lines.append("")

    notes = str(values.get(STEP_NOTES_KEY, "") or "").strip()
    if notes:
        lines.append("**备注 / Markdown 记录**：")
        lines.append("")
        lines.append(notes)
        lines.append("")

    if voice_notes:
        lines.append("**语音速记**：")
        lines.append("")
        lines.extend(_render_voice_notes(voice_notes))
        lines.append("")

    # Timer info
    if step.timer_seconds > 0 or step.timer_override_seconds is not None:
        planned = step.timer_override_seconds if step.timer_override_seconds is not None \
                  else step.timer_seconds
        lines.append("**计时**：")
        lines.append("")

        if step.timer_finished_at:
            lines.append(f"- 计划时长：{_fmt_seconds(planned)}")
            lines.append(f"- 计时结束：{_fmt_dt(step.timer_finished_at)}")
            if step.overtime_seconds > 0:
                lines.append(
                    f"- 超时确认：+{_fmt_seconds(step.overtime_seconds)} "
                    f"（用户于 {_fmt_dt(step.completed_at or step.timer_finished_at)} 确认）"
                )
            else:
                lines.append("- ✅ 按时完成")
        else:
            lines.append(f"- 计划时长：{_fmt_seconds(planned)}")
            if step.timer_override_seconds is not None and step.timer_override_seconds != step.timer_seconds:
                lines.append(f"  *(原始：{_fmt_seconds(step.timer_seconds)}，已修改)*")
        if timer_events:
            lines.extend(_render_timer_events(timer_events))
        lines.append("")

    # Attachments / Photos
    attachments = step.get_attachments()
    if attachments:
        lines.append("**附件 / 照片**：")
        lines.append("")
        for item in attachments:
            path = item["path"]
            name = item["name"]
            lines.append(f"- **{name}**：`{path}`")
            lines.append("")
            if _is_image_attachment(path):
                lines.append(f"![{name}](../photos/{path})")
            else:
                lines.append(f"[打开 {name}](../photos/{path})")
            lines.append("")
        lines.append("")
    elif step.has_camera and step.photo_pending:
        lines.append("**照片**：⚠️ 已跳过（待补）")
        lines.append("")

    # Completion time
    if step.completed_at:
        lines.append(f"**完成时间**：{_fmt_dt(step.completed_at)}")
        lines.append("")

    return lines


def _is_image_attachment(path: str) -> bool:
    return os.path.splitext(path.lower())[1] in {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"
    }


def _render_timer_events(events: list[dict]) -> list[str]:
    lines: list[str] = []
    useful = [
        e for e in sorted(events, key=lambda x: (str(x.get("created_at", "")), int(x.get("id", 0) or 0)))
        if e.get("action") in {"start", "pause", "reset", "confirm", "override"}
    ]
    if not useful:
        return lines

    reset_events = [e for e in useful if e.get("action") == "reset"]
    segments: list[int] = []
    last_reset_idx = -1
    for idx, event in enumerate(useful):
        if event.get("action") == "reset":
            segments.append(int(event.get("elapsed_seconds") or 0))
            last_reset_idx = idx

    tail_events = useful[last_reset_idx + 1:]
    tail_elapsed = max((int(e.get("elapsed_seconds") or 0) for e in tail_events), default=0)
    if tail_elapsed or not segments:
        segments.append(tail_elapsed)

    total_elapsed = sum(segments)
    lines.append(f"- 实际累计计时：{_fmt_seconds(total_elapsed)}")
    if reset_events:
        lines.append(f"- Reset 次数：{len(reset_events)}")
        for i, seconds in enumerate(segments, 1):
            label = "当前段" if i == len(segments) and useful[-1].get("action") != "reset" else f"第 {i} 段"
            lines.append(f"  - {label}：{_fmt_seconds(seconds)}")

    lines.append("- 计时操作记录：")
    for event in useful:
        action = _timer_action_label(str(event.get("action", "")))
        elapsed = _fmt_seconds(int(event.get("elapsed_seconds") or 0))
        lines.append(f"  - {_fmt_dt(event.get('created_at'))} · {action} · 当段已计时 {elapsed}")
    return lines


def _timer_action_label(action: str) -> str:
    return {
        "start": "开始/继续",
        "pause": "暂停",
        "reset": "重置",
        "confirm": "确认结束",
        "override": "修改时长",
    }.get(action, action)


# ─────────────────────────────────────────────
# Storage table
# ─────────────────────────────────────────────

def _render_storage_table(items: list[StorageItem],
                           boxes: dict[int, Box]) -> list[str]:
    lines: list[str] = []
    lines.append("| 样品 | 管型 | Box | 位置 | 备注 |")
    lines.append("|------|------|-----|------|------|")
    for item in items:
        box_name = boxes[item.box_id].box_name if item.box_id and item.box_id in boxes else "—"
        position = item.position or "—"
        notes = item.notes or "—"
        lines.append(
            f"| {item.item_label} | {item.tube_type or '—'} "
            f"| {box_name} | {position} | {notes} |"
        )
    return lines


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _fmt_dt(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(tz=None)  # local time
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str


def _fmt_seconds(seconds: int) -> str:
    if seconds <= 0:
        return "0 秒"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    parts = []
    if h:
        parts.append(f"{h} 小时")
    if m:
        parts.append(f"{m} 分钟")
    if s:
        parts.append(f"{s} 秒")
    return " ".join(parts)


def _status_label(status: str) -> str:
    return {
        "active": "进行中",
        "needs_wrapup": "待收尾",
        "completed": "已完成",
        "archived": "已归档",
    }.get(status, status)
