"""
ELN App — Data Models
All dataclasses representing database rows and domain objects.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal, Optional
import json
import os
import re


# ─────────────────────────────────────────────
# Enums / Literals
# ─────────────────────────────────────────────

ExperimentStatus = Literal["active", "needs_wrapup", "completed", "archived"]
TimerStatus = Literal["idle", "running", "paused", "overtime", "confirmed"]


# ─────────────────────────────────────────────
# Protocol domain objects (not stored directly)
# ─────────────────────────────────────────────

@dataclass
class ProtocolField:
    key: str
    label: str
    type: Literal["text", "number", "dropdown"]
    default: str = ""
    required: bool = False
    options: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "ProtocolField":
        options = d.get("options") or []
        return cls(
            key=d.get("key", ""),
            label=d.get("label", ""),
            type=d.get("type", "text"),
            default=str(d.get("default", "")),
            required=bool(d.get("required", False)),
            options=options,
        )

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "required": self.required,
            "options": self.options,
        }


@dataclass
class ProtocolStep:
    title: str
    description: str
    timer_seconds: int = 0
    has_camera: bool = False
    camera_required: bool = False
    fields: list[ProtocolField] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "ProtocolStep":
        timer_seconds = d.get("timer_seconds", 0)
        if timer_seconds is None:
            timer_seconds = 0
        step = cls(
            title=d.get("title", ""),
            description=d.get("description", ""),
            timer_seconds=int(timer_seconds),
            has_camera=bool(d.get("has_camera", False)),
            camera_required=bool(d.get("camera_required", False)),
            fields=[ProtocolField.from_dict(f) for f in d.get("fields", [])],
        )
        step.ensure_unique_field_keys()
        return step

    def ensure_unique_field_keys(self) -> None:
        used: set[str] = set()
        for index, protocol_field in enumerate(self.fields, 1):
            base = str(protocol_field.key or "").strip()
            if not base:
                base = re.sub(
                    r"[^a-z0-9]+",
                    "_",
                    str(protocol_field.label or "").lower().replace("µ", "u"),
                ).strip("_")
            if not base:
                base = f"field_{index}"
            candidate = base
            suffix = 2
            while candidate in used:
                candidate = f"{base}_{suffix}"
                suffix += 1
            protocol_field.key = candidate
            used.add(candidate)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "timer_seconds": self.timer_seconds,
            "has_camera": self.has_camera,
            "camera_required": self.camera_required,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass
class StorageItemTemplate:
    key: str
    label: str
    tube_type: str = ""
    default_box: str = ""
    notes_template: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "StorageItemTemplate":
        return cls(
            key=d.get("key", ""),
            label=d.get("label", ""),
            tube_type=d.get("tube_type", ""),
            default_box=d.get("default_box", ""),
            notes_template=d.get("notes_template", ""),
        )

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "tube_type": self.tube_type,
            "default_box": self.default_box,
            "notes_template": self.notes_template,
        }


@dataclass
class ProtocolDefinition:
    protocol_name: str
    version: str = "1.0"
    author: str = ""
    steps: list[ProtocolStep] = field(default_factory=list)
    storage_items: list[StorageItemTemplate] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "ProtocolDefinition":
        return cls(
            protocol_name=d.get("protocol_name", "Unnamed Protocol"),
            version=str(d.get("version", "1.0")),
            author=d.get("author", ""),
            steps=[ProtocolStep.from_dict(s) for s in d.get("steps", [])],
            storage_items=[StorageItemTemplate.from_dict(i) for i in d.get("storage_items", [])],
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ProtocolDefinition":
        return cls.from_dict(json.loads(json_str))

    def to_dict(self) -> dict:
        return {
            "protocol_name": self.protocol_name,
            "version": self.version,
            "author": self.author,
            "steps": [s.to_dict() for s in self.steps],
            "storage_items": [i.to_dict() for i in self.storage_items],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# Database row models
# ─────────────────────────────────────────────

@dataclass
class Experiment:
    id: int
    name: str
    created_at: str
    status: ExperimentStatus
    protocol_json: str          # JSON snapshot of ProtocolDefinition
    protocol_id: Optional[int]  # FK to protocols table (nullable)
    notes: str

    def get_protocol(self) -> ProtocolDefinition:
        return ProtocolDefinition.from_json(self.protocol_json)

    @classmethod
    def from_row(cls, row: tuple) -> "Experiment":
        return cls(
            id=row[0], name=row[1], created_at=row[2],
            status=row[3], protocol_json=row[4],
            protocol_id=row[5], notes=row[6] or "",
        )


@dataclass
class Step:
    id: int
    experiment_id: int
    step_index: int
    title: str
    description: str
    timer_seconds: int
    timer_override_seconds: Optional[int]   # user-modified total duration
    timer_finished_at: Optional[str]        # ISO timestamp when countdown hit 0
    overtime_seconds: int                   # seconds past timer end (written on confirm)
    has_camera: bool
    camera_required: bool
    fields_json: str                        # JSON list of ProtocolField dicts
    values_json: str                        # JSON dict {key: value}
    description_overrides_json: str         # JSON dict {original_number: new_number}
    photo_paths: str                        # JSON list of photo path strings
    photo_pending: bool                     # True if user skipped photo
    completed_at: Optional[str]

    @property
    def effective_timer_seconds(self) -> int:
        """Return user-overridden duration if set, else original."""
        return self.timer_override_seconds if self.timer_override_seconds is not None else self.timer_seconds

    def get_fields(self) -> list[ProtocolField]:
        data = json.loads(self.fields_json or "[]")
        return [ProtocolField.from_dict(f) for f in data]

    def get_values(self) -> dict[str, Any]:
        return json.loads(self.values_json or "{}")

    def get_description_overrides(self) -> dict[str, str]:
        return json.loads(self.description_overrides_json or "{}")

    def get_photo_paths(self) -> list[str]:
        return [item["path"] for item in self.get_attachments()]

    def get_attachments(self) -> list[dict[str, str]]:
        """Return normalized attachment metadata, including legacy path strings."""
        try:
            raw_items = json.loads(self.photo_paths or "[]")
        except (TypeError, ValueError):
            raw_items = []
        attachments = []
        for item in raw_items:
            if isinstance(item, str):
                path = item
                name = os.path.basename(path.replace("\\", "/")) or path
            elif isinstance(item, dict):
                path = str(item.get("path", "")).strip()
                name = str(item.get("name", "")).strip()
                if not name:
                    name = os.path.basename(path.replace("\\", "/")) or path
            else:
                continue
            if path:
                attachments.append({"path": path, "name": name})
        return attachments

    @classmethod
    def from_row(cls, row: tuple) -> "Step":
        return cls(
            id=row[0], experiment_id=row[1], step_index=row[2],
            title=row[3], description=row[4],
            timer_seconds=row[5] or 0,
            timer_override_seconds=row[6],
            timer_finished_at=row[7],
            overtime_seconds=row[8] or 0,
            has_camera=bool(row[9]),
            camera_required=bool(row[10]),
            fields_json=row[11] or "[]",
            values_json=row[12] or "{}",
            description_overrides_json=row[13] or "{}",
            photo_paths=row[14] or "[]",
            photo_pending=bool(row[15]),
            completed_at=row[16],
        )


@dataclass
class TimerRecord:
    id: str                     # "{experiment_id}_{step_id}"
    experiment_id: int
    step_id: int
    total_seconds: int
    remaining_seconds: int
    overtime_seconds: int
    status: TimerStatus
    timer_finished_at: Optional[str]
    started_at: Optional[str]
    updated_at: str

    @classmethod
    def from_row(cls, row: tuple) -> "TimerRecord":
        return cls(
            id=row[0], experiment_id=row[1], step_id=row[2],
            total_seconds=row[3] or 0,
            remaining_seconds=row[4] or 0,
            overtime_seconds=row[5] or 0,
            status=row[6] or "idle",
            timer_finished_at=row[7],
            started_at=row[8],
            updated_at=row[9] or "",
        )


@dataclass
class Protocol:
    id: int
    name: str
    version: str
    author: str
    protocol_json: str
    created_at: str
    updated_at: str
    use_count: int
    last_used_at: Optional[str]

    def get_definition(self) -> ProtocolDefinition:
        return ProtocolDefinition.from_json(self.protocol_json)

    @classmethod
    def from_row(cls, row: tuple) -> "Protocol":
        return cls(
            id=row[0], name=row[1], version=row[2], author=row[3],
            protocol_json=row[4], created_at=row[5], updated_at=row[6],
            use_count=row[7] or 0, last_used_at=row[8],
        )


@dataclass
class Box:
    id: int
    box_name: str
    box_size: int               # 9 or 10
    created_at: str
    notes: str

    @classmethod
    def from_row(cls, row: tuple) -> "Box":
        return cls(
            id=row[0], box_name=row[1], box_size=row[2] or 10,
            created_at=row[3], notes=row[4] or "",
        )


@dataclass
class BoxSlot:
    id: int
    box_id: int
    row_label: str              # A-J
    col_label: str              # 1-10
    sample_name: str
    notes: str
    experiment_id: Optional[int]
    step_id: Optional[int]
    created_at: str

    @property
    def position(self) -> str:
        return f"{self.row_label}{self.col_label}"

    @classmethod
    def from_row(cls, row: tuple) -> "BoxSlot":
        return cls(
            id=row[0], box_id=row[1], row_label=row[2], col_label=row[3],
            sample_name=row[4] or "", notes=row[5] or "",
            experiment_id=row[6], step_id=row[7], created_at=row[8],
        )


@dataclass
class StorageItem:
    id: int
    experiment_id: int
    item_key: str
    item_label: str
    tube_type: str
    notes_template: str
    default_box: str
    box_id: Optional[int]
    row_label: Optional[str]
    col_label: Optional[str]
    notes: str
    registered_at: Optional[str]

    @property
    def is_registered(self) -> bool:
        return self.box_id is not None and self.row_label is not None

    @property
    def position(self) -> Optional[str]:
        if self.row_label and self.col_label:
            return f"{self.row_label}{self.col_label}"
        return None

    @classmethod
    def from_row(cls, row: tuple) -> "StorageItem":
        return cls(
            id=row[0], experiment_id=row[1], item_key=row[2],
            item_label=row[3], tube_type=row[4] or "",
            notes_template=row[5] or "", default_box=row[6] or "",
            box_id=row[7], row_label=row[8], col_label=row[9],
            notes=row[10] or "", registered_at=row[11],
        )
