"""
ELN App — Database Layer
SQLite connection, schema creation, and all CRUD operations.
"""

from __future__ import annotations
import sqlite3
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Optional, Any
from contextlib import contextmanager

from db.models import (
    Experiment, Step, TimerRecord, Protocol, Box, BoxSlot, StorageItem,
    ProtocolDefinition, ProtocolField, ProtocolStep, ExperimentStatus,
)


# ─────────────────────────────────────────────
# Path resolution
# ─────────────────────────────────────────────

def get_db_path() -> str:
    """Return platform-appropriate database path."""
    import platform
    if platform.system() == "Windows":
        base = os.path.join(os.path.expanduser("~"), "ELN_Data")
    else:
        base = os.path.join(os.path.expanduser("~"), "ELN_Data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "eln.db")


def get_photos_dir() -> str:
    import platform
    if platform.system() == "Windows":
        base = os.path.join(os.path.expanduser("~"), "ELN_Data", "photos")
    else:
        base = os.path.join(os.path.expanduser("~"), "ELN_Data", "photos")
    os.makedirs(base, exist_ok=True)
    return base


def get_uploads_dir() -> str:
    base = os.path.join(os.path.expanduser("~"), "ELN_Data", "uploads")
    os.makedirs(base, exist_ok=True)
    return base


def get_reports_dir() -> str:
    base = os.path.join(os.path.expanduser("~"), "ELN_Data", "reports")
    os.makedirs(base, exist_ok=True)
    return base


# ─────────────────────────────────────────────
# Connection management
# ─────────────────────────────────────────────

_DB_PATH: Optional[str] = None


def init_db(path: Optional[str] = None) -> None:
    """Initialize database: set path and create all tables."""
    global _DB_PATH
    _DB_PATH = path or get_db_path()
    _create_tables()


def get_connection() -> sqlite3.Connection:
    if _DB_PATH is None:
        init_db()
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_conn():
    """Context manager yielding a connection that auto-commits or rolls back."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────
# Schema creation
# ─────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'active',
    protocol_json   TEXT    NOT NULL,
    protocol_id     INTEGER,
    notes           TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS steps (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id               INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    step_index                  INTEGER NOT NULL,
    title                       TEXT    NOT NULL DEFAULT '',
    description                 TEXT    NOT NULL DEFAULT '',
    timer_seconds               INTEGER NOT NULL DEFAULT 0,
    timer_override_seconds      INTEGER,
    timer_finished_at           TEXT,
    overtime_seconds            INTEGER NOT NULL DEFAULT 0,
    has_camera                  INTEGER NOT NULL DEFAULT 0,
    camera_required             INTEGER NOT NULL DEFAULT 0,
    fields_json                 TEXT    NOT NULL DEFAULT '[]',
    values_json                 TEXT    NOT NULL DEFAULT '{}',
    description_overrides_json  TEXT    NOT NULL DEFAULT '{}',
    photo_paths                 TEXT    NOT NULL DEFAULT '[]',
    photo_pending               INTEGER NOT NULL DEFAULT 0,
    completed_at                TEXT
);

CREATE TABLE IF NOT EXISTS timers (
    id                  TEXT    PRIMARY KEY,
    experiment_id       INTEGER NOT NULL,
    step_id             INTEGER NOT NULL,
    total_seconds       INTEGER NOT NULL DEFAULT 0,
    remaining_seconds   INTEGER NOT NULL DEFAULT 0,
    overtime_seconds    INTEGER NOT NULL DEFAULT 0,
    status              TEXT    NOT NULL DEFAULT 'idle',
    timer_finished_at   TEXT,
    started_at          TEXT,
    updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS timer_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id       INTEGER NOT NULL,
    step_id             INTEGER NOT NULL,
    action              TEXT    NOT NULL,
    total_seconds       INTEGER NOT NULL DEFAULT 0,
    remaining_seconds   INTEGER NOT NULL DEFAULT 0,
    overtime_seconds    INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds     INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    notes               TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS protocols (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    version         TEXT    NOT NULL DEFAULT '1.0',
    author          TEXT    NOT NULL DEFAULT '',
    protocol_json   TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    use_count       INTEGER NOT NULL DEFAULT 0,
    last_used_at    TEXT
);

CREATE TABLE IF NOT EXISTS boxes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    box_name    TEXT    NOT NULL,
    box_size    INTEGER NOT NULL DEFAULT 10,
    created_at  TEXT    NOT NULL,
    notes       TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS box_slots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id          INTEGER NOT NULL REFERENCES boxes(id) ON DELETE CASCADE,
    row_label       TEXT    NOT NULL,
    col_label       TEXT    NOT NULL,
    sample_name     TEXT    NOT NULL DEFAULT '',
    notes           TEXT    DEFAULT '',
    experiment_id   INTEGER,
    step_id         INTEGER,
    created_at      TEXT    NOT NULL,
    UNIQUE(box_id, row_label, col_label)
);

CREATE TABLE IF NOT EXISTS storage_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    item_key        TEXT    NOT NULL,
    item_label      TEXT    NOT NULL,
    tube_type       TEXT    DEFAULT '',
    notes_template  TEXT    DEFAULT '',
    default_box     TEXT    DEFAULT '',
    box_id          INTEGER,
    row_label       TEXT,
    col_label       TEXT,
    notes           TEXT    DEFAULT '',
    registered_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_steps_experiment ON steps(experiment_id, step_index);
CREATE INDEX IF NOT EXISTS idx_timers_experiment ON timers(experiment_id);
CREATE INDEX IF NOT EXISTS idx_timer_events_step ON timer_events(experiment_id, step_id, created_at);
CREATE INDEX IF NOT EXISTS idx_box_slots_box ON box_slots(box_id);
CREATE INDEX IF NOT EXISTS idx_storage_experiment ON storage_items(experiment_id);
"""


def _create_tables() -> None:
    with db_conn() as conn:
        conn.executescript(_SCHEMA)
        _normalize_existing_step_field_keys(conn)


def _normalize_existing_step_field_keys(conn: sqlite3.Connection) -> None:
    """Repair legacy steps whose field keys are empty or duplicated."""
    rows = conn.execute(
        "SELECT id, fields_json, values_json FROM steps"
    ).fetchall()
    for row in rows:
        try:
            raw_fields = json.loads(row["fields_json"] or "[]")
            old_values = json.loads(row["values_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(raw_fields, list) or not isinstance(old_values, dict):
            continue

        fields = [ProtocolField.from_dict(item) for item in raw_fields if isinstance(item, dict)]
        old_keys = [field.key for field in fields]
        step_def = ProtocolStep(title="", description="", fields=fields)
        step_def.ensure_unique_field_keys()
        new_keys = [field.key for field in fields]
        if old_keys == new_keys:
            continue

        last_index_by_key = {
            key: index
            for index, key in enumerate(old_keys)
        }
        new_values = {
            key: value
            for key, value in old_values.items()
            if key not in set(old_keys)
        }
        for index, (old_key, new_key) in enumerate(zip(old_keys, new_keys)):
            if old_key in old_values and last_index_by_key.get(old_key) == index:
                new_values[new_key] = old_values[old_key]

        conn.execute(
            "UPDATE steps SET fields_json=?, values_json=? WHERE id=?",
            (
                json.dumps([field.to_dict() for field in fields], ensure_ascii=False),
                json.dumps(new_values, ensure_ascii=False),
                row["id"],
            ),
        )


# ─────────────────────────────────────────────
# Experiments CRUD
# ─────────────────────────────────────────────

def create_experiment(name: str, protocol: ProtocolDefinition,
                      protocol_id: Optional[int] = None,
                      notes: str = "") -> Experiment:
    """Create experiment + all steps from protocol definition."""
    now = _now()
    with db_conn() as conn:
        cur = conn.execute(
            "INSERT INTO experiments (name, created_at, status, protocol_json, protocol_id, notes) "
            "VALUES (?, ?, 'active', ?, ?, ?)",
            (name, now, protocol.to_json(), protocol_id, notes),
        )
        exp_id = cur.lastrowid

        for idx, step_def in enumerate(protocol.steps):
            step_def.ensure_unique_field_keys()
            conn.execute(
                """INSERT INTO steps
                   (experiment_id, step_index, title, description, timer_seconds,
                    has_camera, camera_required, fields_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    exp_id, idx,
                    step_def.title, step_def.description, step_def.timer_seconds,
                    int(step_def.has_camera), int(step_def.camera_required),
                    json.dumps([f.to_dict() for f in step_def.fields], ensure_ascii=False),
                ),
            )

        # Create storage_items rows from protocol template
        for item in protocol.storage_items:
            conn.execute(
                """INSERT INTO storage_items
                   (experiment_id, item_key, item_label, tube_type, notes_template, default_box)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (exp_id, item.key, item.label, item.tube_type,
                 item.notes_template, item.default_box),
            )

        # Update protocol use_count if linked
        if protocol_id is not None:
            conn.execute(
                "UPDATE protocols SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
                (now, protocol_id),
            )

    return get_experiment(exp_id)


def get_experiment(exp_id: int) -> Optional[Experiment]:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT id, name, created_at, status, protocol_json, protocol_id, notes "
            "FROM experiments WHERE id = ?", (exp_id,)
        ).fetchone()
    return Experiment.from_row(tuple(row)) if row else None


def list_experiments(status: Optional[ExperimentStatus] = None) -> list[Experiment]:
    with db_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT id, name, created_at, status, protocol_json, protocol_id, notes "
                "FROM experiments WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, created_at, status, protocol_json, protocol_id, notes "
                "FROM experiments ORDER BY created_at DESC"
            ).fetchall()
    return [Experiment.from_row(tuple(r)) for r in rows]


def update_experiment(exp_id: int, **kwargs) -> Optional[Experiment]:
    """Update any subset of: name, status, notes."""
    allowed = {"name", "status", "notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_experiment(exp_id)
    sets = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [exp_id]
    with db_conn() as conn:
        conn.execute(f"UPDATE experiments SET {sets} WHERE id = ?", vals)
    return get_experiment(exp_id)


def delete_experiment(exp_id: int) -> bool:
    with db_conn() as conn:
        conn.execute("DELETE FROM experiments WHERE id = ?", (exp_id,))
    return True


# ─────────────────────────────────────────────
# Steps CRUD
# ─────────────────────────────────────────────

def get_steps(experiment_id: int) -> list[Step]:
    with db_conn() as conn:
        rows = conn.execute(
            """SELECT id, experiment_id, step_index, title, description,
                      timer_seconds, timer_override_seconds, timer_finished_at,
                      overtime_seconds, has_camera, camera_required,
                      fields_json, values_json, description_overrides_json,
                      photo_paths, photo_pending, completed_at
               FROM steps WHERE experiment_id = ? ORDER BY step_index""",
            (experiment_id,)
        ).fetchall()
    return [Step.from_row(tuple(r)) for r in rows]


def get_step(step_id: int) -> Optional[Step]:
    with db_conn() as conn:
        row = conn.execute(
            """SELECT id, experiment_id, step_index, title, description,
                      timer_seconds, timer_override_seconds, timer_finished_at,
                      overtime_seconds, has_camera, camera_required,
                      fields_json, values_json, description_overrides_json,
                      photo_paths, photo_pending, completed_at
               FROM steps WHERE id = ?""",
            (step_id,)
        ).fetchone()
    return Step.from_row(tuple(row)) if row else None


def update_step(step_id: int, **kwargs) -> Optional[Step]:
    """Update any subset of step fields."""
    allowed = {
        "title", "description", "fields_json",
        "values_json", "description_overrides_json", "photo_paths",
        "photo_pending", "timer_override_seconds", "timer_finished_at",
        "overtime_seconds", "completed_at",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_step(step_id)
    sets = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [step_id]
    with db_conn() as conn:
        conn.execute(f"UPDATE steps SET {sets} WHERE id = ?", vals)
    return get_step(step_id)


def complete_step(step_id: int) -> Optional[Step]:
    return update_step(step_id, completed_at=_now())


def _attachment_name(name: str, path: str) -> str:
    cleaned = " ".join(str(name or "").strip().split())
    return cleaned[:240] or os.path.basename(path.replace("\\", "/")) or path


def add_photo_to_step(
    step_id: int,
    photo_path: str,
    attachment_name: str = "",
) -> Optional[Step]:
    step = get_step(step_id)
    if step is None:
        return None
    attachments = step.get_attachments()
    attachments.append({
        "path": photo_path,
        "name": _attachment_name(attachment_name, photo_path),
    })
    return update_step(
        step_id,
        photo_paths=json.dumps(attachments, ensure_ascii=False),
        photo_pending=0,
    )


def rename_attachment(step_id: int, photo_path: str, attachment_name: str) -> Optional[Step]:
    step = get_step(step_id)
    if step is None:
        return None
    attachments = step.get_attachments()
    for item in attachments:
        if item["path"] == photo_path:
            item["name"] = _attachment_name(attachment_name, photo_path)
            return update_step(
                step_id,
                photo_paths=json.dumps(attachments, ensure_ascii=False),
            )
    raise ValueError("Attachment not found")


def upload_photo(step_id: int, file_path: str) -> dict[str, str]:
    """Copy a local photo into ELN_Data/photos and attach it to a step."""
    step = get_step(step_id)
    if step is None:
        raise ValueError(f"Step not found: {step_id}")
    if not os.path.exists(file_path) or os.path.getsize(file_path) <= 0:
        raise ValueError("照片文件为空，请重新拍照或选择图片")
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)

    sub_dir = os.path.join(get_photos_dir(), str(step.experiment_id))
    os.makedirs(sub_dir, exist_ok=True)

    ext = os.path.splitext(file_path)[1] or ".jpg"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"step{step_id}_{ts}{ext}"
    dest = os.path.join(sub_dir, filename)
    shutil.copy2(file_path, dest)

    rel_path = f"{step.experiment_id}/{filename}"
    display_name = os.path.basename(file_path)
    add_photo_to_step(step_id, rel_path, display_name)
    return {"path": rel_path, "name": display_name, "url": photo_url(rel_path)}


def upload_photo_bytes(step_id: int, filename: str, data: bytes) -> dict[str, str]:
    """Save uploaded image bytes into ELN_Data/photos and attach it to a step."""
    step = get_step(step_id)
    if step is None:
        raise ValueError(f"Step not found: {step_id}")

    sub_dir = os.path.join(get_photos_dir(), str(step.experiment_id))
    os.makedirs(sub_dir, exist_ok=True)

    ext = os.path.splitext(filename or "photo.jpg")[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}:
        ext = ".jpg"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"step{step_id}_{ts}{ext}"
    filepath = os.path.join(sub_dir, out_name)

    with open(filepath, "wb") as f:
        f.write(data)

    rel_path = f"{step.experiment_id}/{out_name}"
    display_name = os.path.basename(filename or out_name)
    add_photo_to_step(step_id, rel_path, display_name)
    return {"path": rel_path, "name": display_name, "url": photo_url(rel_path)}


def photo_url(rel_path: str) -> str:
    """Return a local path Flet can preview in Windows desktop mode."""
    return os.path.join(get_photos_dir(), rel_path.replace("/", os.sep))


def get_pending_photo_steps(experiment_id: int) -> list[Step]:
    """Return steps where photo was skipped (photo_pending=1)."""
    with db_conn() as conn:
        rows = conn.execute(
            """SELECT id, experiment_id, step_index, title, description,
                      timer_seconds, timer_override_seconds, timer_finished_at,
                      overtime_seconds, has_camera, camera_required,
                      fields_json, values_json, description_overrides_json,
                      photo_paths, photo_pending, completed_at
               FROM steps
               WHERE experiment_id = ? AND has_camera = 1 AND photo_pending = 1
               ORDER BY step_index""",
            (experiment_id,)
        ).fetchall()
    return [Step.from_row(tuple(r)) for r in rows]


def get_pending_photos(experiment_id: int) -> list[Step]:
    return get_pending_photo_steps(experiment_id)


# ─────────────────────────────────────────────
# Timers CRUD
# ─────────────────────────────────────────────

def _timer_id(experiment_id: int, step_id: int) -> str:
    return f"{experiment_id}_{step_id}"


def get_timer(experiment_id: int, step_id: int) -> Optional[TimerRecord]:
    tid = _timer_id(experiment_id, step_id)
    with db_conn() as conn:
        row = conn.execute(
            """SELECT id, experiment_id, step_id, total_seconds, remaining_seconds,
                      overtime_seconds, status, timer_finished_at, started_at, updated_at
               FROM timers WHERE id = ?""",
            (tid,)
        ).fetchone()
    return TimerRecord.from_row(tuple(row)) if row else None


def upsert_timer(experiment_id: int, step_id: int, total_seconds: int,
                 remaining_seconds: int, overtime_seconds: int,
                 status: str, timer_finished_at: Optional[str] = None,
                 started_at: Optional[str] = None) -> TimerRecord:
    tid = _timer_id(experiment_id, step_id)
    now = _now()
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO timers
               (id, experiment_id, step_id, total_seconds, remaining_seconds,
                overtime_seconds, status, timer_finished_at, started_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 total_seconds = excluded.total_seconds,
                 remaining_seconds = excluded.remaining_seconds,
                 overtime_seconds = excluded.overtime_seconds,
                 status = excluded.status,
                 timer_finished_at = excluded.timer_finished_at,
                 started_at = COALESCE(timers.started_at, excluded.started_at),
                 updated_at = excluded.updated_at""",
            (tid, experiment_id, step_id, total_seconds, remaining_seconds,
             overtime_seconds, status, timer_finished_at, started_at, now),
        )
    return get_timer(experiment_id, step_id)


def list_active_timers() -> list[TimerRecord]:
    """Return all timers in running or overtime state (for background restore)."""
    with db_conn() as conn:
        rows = conn.execute(
            """SELECT id, experiment_id, step_id, total_seconds, remaining_seconds,
                      overtime_seconds, status, timer_finished_at, started_at, updated_at
               FROM timers WHERE status IN ('running', 'overtime')"""
        ).fetchall()
    return [TimerRecord.from_row(tuple(r)) for r in rows]


def log_timer_event(
    experiment_id: int,
    step_id: int,
    action: str,
    total_seconds: int = 0,
    remaining_seconds: int = 0,
    overtime_seconds: int = 0,
    elapsed_seconds: int = 0,
    notes: str = "",
) -> None:
    """Append a human-meaningful timer action for report/audit history."""
    action = (action or "").strip().lower()
    if not action:
        return
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO timer_events
               (experiment_id, step_id, action, total_seconds, remaining_seconds,
                overtime_seconds, elapsed_seconds, created_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                experiment_id,
                step_id,
                action,
                max(0, int(total_seconds or 0)),
                max(0, int(remaining_seconds or 0)),
                max(0, int(overtime_seconds or 0)),
                max(0, int(elapsed_seconds or 0)),
                _now(),
                notes or "",
            ),
        )


def list_timer_events(experiment_id: int) -> list[dict[str, Any]]:
    with db_conn() as conn:
        rows = conn.execute(
            """SELECT id, experiment_id, step_id, action, total_seconds,
                      remaining_seconds, overtime_seconds, elapsed_seconds,
                      created_at, notes
               FROM timer_events
               WHERE experiment_id = ?
               ORDER BY step_id, created_at, id""",
            (experiment_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# Protocols CRUD
# ─────────────────────────────────────────────

def _coerce_protocol_definition(definition: ProtocolDefinition | str | dict) -> ProtocolDefinition:
    if isinstance(definition, ProtocolDefinition):
        return definition
    if isinstance(definition, str):
        return ProtocolDefinition.from_json(definition)
    if isinstance(definition, dict):
        return ProtocolDefinition.from_dict(definition)
    raise TypeError("protocol must be a ProtocolDefinition, JSON string, or dict")


def create_protocol(definition: ProtocolDefinition | str | dict) -> Protocol:
    definition = _coerce_protocol_definition(definition)
    now = _now()
    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO protocols (name, version, author, protocol_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (definition.protocol_name, definition.version, definition.author,
             definition.to_json(), now, now),
        )
        pid = cur.lastrowid
    return get_protocol(pid)


def get_protocol(protocol_id: int) -> Optional[Protocol]:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT id, name, version, author, protocol_json, created_at, updated_at, "
            "use_count, last_used_at FROM protocols WHERE id = ?",
            (protocol_id,)
        ).fetchone()
    return Protocol.from_row(tuple(row)) if row else None


def list_protocols() -> list[Protocol]:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, version, author, protocol_json, created_at, updated_at, "
            "use_count, last_used_at FROM protocols ORDER BY last_used_at DESC NULLS LAST, created_at DESC"
        ).fetchall()
    return [Protocol.from_row(tuple(r)) for r in rows]


def update_protocol(protocol_id: int, definition: ProtocolDefinition | str | dict) -> Optional[Protocol]:
    definition = _coerce_protocol_definition(definition)
    now = _now()
    with db_conn() as conn:
        conn.execute(
            """UPDATE protocols SET name=?, version=?, author=?, protocol_json=?, updated_at=?
               WHERE id=?""",
            (definition.protocol_name, definition.version, definition.author,
             definition.to_json(), now, protocol_id),
        )
    return get_protocol(protocol_id)


def delete_protocol(protocol_id: int) -> bool:
    with db_conn() as conn:
        conn.execute("DELETE FROM protocols WHERE id = ?", (protocol_id,))
    return True


# ─────────────────────────────────────────────
# Boxes CRUD
# ─────────────────────────────────────────────

def create_box(box_name: str, box_size: int = 10, notes: str = "") -> Box:
    now = _now()
    with db_conn() as conn:
        cur = conn.execute(
            "INSERT INTO boxes (box_name, box_size, created_at, notes) VALUES (?, ?, ?, ?)",
            (box_name, box_size, now, notes),
        )
        bid = cur.lastrowid
    return get_box(bid)


def get_box(box_id: int) -> Optional[Box]:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT id, box_name, box_size, created_at, notes FROM boxes WHERE id = ?",
            (box_id,)
        ).fetchone()
    return Box.from_row(tuple(row)) if row else None


def list_boxes() -> list[Box]:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, box_name, box_size, created_at, notes FROM boxes ORDER BY created_at DESC"
        ).fetchall()
    return [Box.from_row(tuple(r)) for r in rows]


def update_box(box_id: int, **kwargs) -> Optional[Box]:
    allowed = {"box_name", "box_size", "notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_box(box_id)
    sets = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [box_id]
    with db_conn() as conn:
        conn.execute(f"UPDATE boxes SET {sets} WHERE id = ?", vals)
    return get_box(box_id)


def delete_box(box_id: int) -> bool:
    with db_conn() as conn:
        conn.execute("DELETE FROM boxes WHERE id = ?", (box_id,))
    return True


# ─────────────────────────────────────────────
# Box Slots CRUD
# ─────────────────────────────────────────────

def get_box_slots(box_id: int) -> list[BoxSlot]:
    with db_conn() as conn:
        rows = conn.execute(
            """SELECT id, box_id, row_label, col_label, sample_name, notes,
                      experiment_id, step_id, created_at
               FROM box_slots WHERE box_id = ? ORDER BY row_label, col_label""",
            (box_id,)
        ).fetchall()
    return [BoxSlot.from_row(tuple(r)) for r in rows]


def get_slots(box_id: int) -> list[BoxSlot]:
    return get_box_slots(box_id)


def get_slot(box_id: int, row_label: str, col_label: str) -> Optional[BoxSlot]:
    with db_conn() as conn:
        row = conn.execute(
            """SELECT id, box_id, row_label, col_label, sample_name, notes,
                      experiment_id, step_id, created_at
               FROM box_slots WHERE box_id=? AND row_label=? AND col_label=?""",
            (box_id, row_label, col_label)
        ).fetchone()
    return BoxSlot.from_row(tuple(row)) if row else None


def upsert_slot(box_id: int, row_label: str, col_label: str,
                sample_name: str, notes: str = "",
                experiment_id: Optional[int] = None,
                step_id: Optional[int] = None) -> BoxSlot:
    now = _now()
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO box_slots
               (box_id, row_label, col_label, sample_name, notes, experiment_id, step_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(box_id, row_label, col_label) DO UPDATE SET
                 sample_name = excluded.sample_name,
                 notes = excluded.notes,
                 experiment_id = excluded.experiment_id,
                 step_id = excluded.step_id""",
            (box_id, row_label, col_label, sample_name, notes,
             experiment_id, step_id, now),
        )
    return get_slot(box_id, row_label, col_label)


def clear_slot(box_id: int, row_label: str, col_label: str) -> bool:
    with db_conn() as conn:
        conn.execute(
            "DELETE FROM box_slots WHERE box_id=? AND row_label=? AND col_label=?",
            (box_id, row_label, col_label)
        )
    return True


def get_box_slot_count(box_id: int) -> int:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM box_slots WHERE box_id = ?", (box_id,)
        ).fetchone()
    return row[0] if row else 0


# ─────────────────────────────────────────────
# Storage Items CRUD
# ─────────────────────────────────────────────

def get_storage_items(experiment_id: int) -> list[StorageItem]:
    with db_conn() as conn:
        rows = conn.execute(
            """SELECT id, experiment_id, item_key, item_label, tube_type,
                      notes_template, default_box, box_id, row_label, col_label,
                      notes, registered_at
               FROM storage_items WHERE experiment_id = ? ORDER BY id""",
            (experiment_id,)
        ).fetchall()
    return [StorageItem.from_row(tuple(r)) for r in rows]


def create_storage_item(experiment_id: int, item_label: str,
                        tube_type: str = "", notes_template: str = "",
                        default_box: str = "", item_key: Optional[str] = None) -> StorageItem:
    key = item_key or f"manual_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO storage_items
               (experiment_id, item_key, item_label, tube_type, notes_template, default_box)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (experiment_id, key, item_label, tube_type, notes_template, default_box),
        )
        item_id = cur.lastrowid
    with db_conn() as conn:
        row = conn.execute(
            """SELECT id, experiment_id, item_key, item_label, tube_type,
                      notes_template, default_box, box_id, row_label, col_label,
                      notes, registered_at
               FROM storage_items WHERE id=?""",
            (item_id,)
        ).fetchone()
    return StorageItem.from_row(tuple(row))


def register_storage_item(item_id: int, box_id: int,
                           row_label: str, col_label: str,
                           notes: str = "", exp_id: Optional[int] = None) -> Optional[StorageItem]:
    now = _now()
    with db_conn() as conn:
        conn.execute(
            """UPDATE storage_items
               SET box_id=?, row_label=?, col_label=?, notes=?, registered_at=?
               WHERE id=?""",
            (box_id, row_label, col_label, notes, now, item_id),
        )
    with db_conn() as conn:
        row = conn.execute(
            """SELECT id, experiment_id, item_key, item_label, tube_type,
                      notes_template, default_box, box_id, row_label, col_label,
                      notes, registered_at
               FROM storage_items WHERE id=?""",
            (item_id,)
        ).fetchone()
    item = StorageItem.from_row(tuple(row)) if row else None
    if item:
        upsert_slot(
            box_id=box_id,
            row_label=row_label,
            col_label=col_label,
            sample_name=item.item_label,
            notes=notes,
            experiment_id=exp_id or item.experiment_id,
        )
    return item


def get_unregistered_storage_items(experiment_id: int) -> list[StorageItem]:
    with db_conn() as conn:
        rows = conn.execute(
            """SELECT id, experiment_id, item_key, item_label, tube_type,
                      notes_template, default_box, box_id, row_label, col_label,
                      notes, registered_at
               FROM storage_items WHERE experiment_id=? AND box_id IS NULL ORDER BY id""",
            (experiment_id,)
        ).fetchall()
    return [StorageItem.from_row(tuple(r)) for r in rows]


def get_report(exp_id: int) -> str:
    from utils.report_generator import generate_report
    exp = get_experiment(exp_id)
    if exp is None:
        raise ValueError(f"Experiment not found: {exp_id}")
    steps = get_steps(exp_id)
    storage_items = get_storage_items(exp_id)
    boxes = {b.id: b for b in list_boxes()}
    timer_events = list_timer_events(exp_id)
    return generate_report(exp, steps, storage_items, boxes, timer_events)


def save_report(exp_id: int) -> dict[str, str]:
    """Write the current Markdown report to ELN_Data/reports."""
    exp = get_experiment(exp_id)
    if exp is None:
        raise ValueError(f"Experiment not found: {exp_id}")
    markdown = get_report(exp_id)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = _safe_filename(exp.name) or f"experiment_{exp_id}"
    filename = f"{safe_name}_{stamp}.md"
    path = os.path.join(get_reports_dir(), filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return {"path": path, "filename": filename}


def _safe_filename(name: str) -> str:
    blocked = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in blocked else ch for ch in (name or "").strip())
    cleaned = cleaned.rstrip(". ")
    return cleaned[:80]


# ─────────────────────────────────────────────
# Experiment progress helpers
# ─────────────────────────────────────────────

def get_experiment_progress(experiment_id: int) -> dict[str, Any]:
    """Return progress and the last completed step time."""
    with db_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM steps WHERE experiment_id=?", (experiment_id,)
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM steps WHERE experiment_id=? AND completed_at IS NOT NULL",
            (experiment_id,)
        ).fetchone()[0]
        completed_at_row = conn.execute(
            "SELECT MAX(completed_at) FROM steps WHERE experiment_id=? AND completed_at IS NOT NULL",
            (experiment_id,)
        ).fetchone()
        # First incomplete step
        row = conn.execute(
            "SELECT step_index FROM steps WHERE experiment_id=? AND completed_at IS NULL "
            "ORDER BY step_index LIMIT 1",
            (experiment_id,)
        ).fetchone()
    current = row[0] if row else (total - 1 if total > 0 else 0)
    completed_at = completed_at_row[0] if completed_at_row else None
    return {
        "total_steps": total,
        "completed_steps": completed,
        "current_step_index": current,
        "completed_at": completed_at,
    }
