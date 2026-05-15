"""
ELN App — API Client
HTTP client for iOS mode (when the app runs on iPhone and talks to Windows server).
Mirrors the same interface as db.database so views can call either transparently.
"""

from __future__ import annotations
import json
import logging
from typing import Any, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

_BASE_URL: str = "http://192.168.1.100:8000"   # default; overridden from settings


def set_base_url(url: str) -> None:
    global _BASE_URL
    _BASE_URL = url.rstrip("/")


def get_base_url() -> str:
    return _BASE_URL


# ─────────────────────────────────────────────
# Low-level HTTP helpers
# ─────────────────────────────────────────────

def _request(method: str, path: str, body: Any = None,
             timeout: int = 10) -> Any:
    url = f"{_BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise APIError(e.code, detail) from e
    except URLError as e:
        raise ConnectionError(f"Cannot reach server at {_BASE_URL}: {e.reason}") from e


def _get(path: str, params: Optional[dict] = None) -> Any:
    if params:
        path = f"{path}?{urlencode({k: v for k, v in params.items() if v is not None})}"
    return _request("GET", path)


def _post(path: str, body: Any = None) -> Any:
    return _request("POST", path, body)


def _put(path: str, body: Any = None) -> Any:
    return _request("PUT", path, body)


def _patch(path: str, body: Any = None) -> Any:
    return _request("PATCH", path, body)


def _delete(path: str) -> None:
    _request("DELETE", path)


class APIError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


# ─────────────────────────────────────────────
# Health / connectivity
# ─────────────────────────────────────────────

def check_connection() -> bool:
    """Return True if server is reachable."""
    try:
        _get("/api/health")
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────
# Experiments
# ─────────────────────────────────────────────

def list_experiments(status: Optional[str] = None) -> list[dict]:
    return _get("/api/experiments", {"status": status})


def create_experiment(name: str, protocol_json: str = None, protocol=None,
                      protocol_id: Optional[int] = None,
                      notes: str = "") -> dict:
    if protocol_json is None and protocol is not None:
        protocol_json = protocol.to_json() if hasattr(protocol, "to_json") else str(protocol)
    return _post("/api/experiments", {
        "name": name, "protocol_json": protocol_json,
        "protocol_id": protocol_id, "notes": notes,
    })


def get_experiment(exp_id: int) -> dict:
    return _get(f"/api/experiments/{exp_id}")


def update_experiment(exp_id: int, **kwargs) -> dict:
    return _patch(f"/api/experiments/{exp_id}", kwargs)


def delete_experiment(exp_id: int) -> None:
    _delete(f"/api/experiments/{exp_id}")


# ─────────────────────────────────────────────
# Steps
# ─────────────────────────────────────────────

def get_steps(exp_id: int) -> list[dict]:
    return _get(f"/api/experiments/{exp_id}/steps")


def get_step(step_id: int) -> dict:
    return _get(f"/api/steps/{step_id}")


def update_step(step_id: int, **kwargs) -> dict:
    return _patch(f"/api/steps/{step_id}", kwargs)


def complete_step(step_id: int) -> dict:
    return _post(f"/api/steps/{step_id}/complete")


def get_pending_photos(exp_id: int) -> list[dict]:
    return _get(f"/api/experiments/{exp_id}/pending_photos")


# ─────────────────────────────────────────────
# Timers
# ─────────────────────────────────────────────

def get_timer(exp_id: int, step_id: int) -> Optional[dict]:
    try:
        return _get(f"/api/timers/{exp_id}/{step_id}")
    except APIError as e:
        if e.status_code == 404:
            return None
        raise


def upsert_timer(exp_id: int, step_id: int, **kwargs) -> dict:
    return _put(f"/api/timers/{exp_id}/{step_id}", kwargs)


# ─────────────────────────────────────────────
# Photos
# ─────────────────────────────────────────────

def upload_photo(step_id: int, file_path: str) -> dict:
    """Upload a photo file to the server. Returns {path, url}."""
    import os
    from urllib.request import urlopen, Request
    import mimetypes

    url = f"{_BASE_URL}/api/photos/upload?step_id={step_id}"
    filename = os.path.basename(file_path)
    mime_type = mimetypes.guess_type(filename)[0] or "image/jpeg"

    boundary = "----ELNBoundary"
    with open(file_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        raise APIError(e.code, e.read().decode()) from e


def photo_url(rel_path: str) -> str:
    return f"{_BASE_URL}/photos/{rel_path}"


# ─────────────────────────────────────────────
# Protocols
# ─────────────────────────────────────────────

def list_protocols() -> list[dict]:
    return _get("/api/protocols")


def create_protocol(protocol_json: str) -> dict:
    return _post("/api/protocols", {"protocol_json": protocol_json})


def get_protocol(protocol_id: int) -> dict:
    return _get(f"/api/protocols/{protocol_id}")


def update_protocol(protocol_id: int, protocol_json: str) -> dict:
    return _put(f"/api/protocols/{protocol_id}", {"protocol_json": protocol_json})


def delete_protocol(protocol_id: int) -> None:
    _delete(f"/api/protocols/{protocol_id}")


# ─────────────────────────────────────────────
# Boxes
# ─────────────────────────────────────────────

def list_boxes() -> list[dict]:
    return _get("/api/boxes")


def create_box(box_name: str, box_size: int = 10, notes: str = "") -> dict:
    return _post("/api/boxes", {"box_name": box_name, "box_size": box_size, "notes": notes})


def get_box(box_id: int) -> dict:
    return _get(f"/api/boxes/{box_id}")


def update_box(box_id: int, **kwargs) -> dict:
    return _patch(f"/api/boxes/{box_id}", kwargs)


def delete_box(box_id: int) -> None:
    _delete(f"/api/boxes/{box_id}")


def get_slots(box_id: int) -> list[dict]:
    return _get(f"/api/boxes/{box_id}/slots")


def upsert_slot(box_id: int, position: str, sample_name: str,
                notes: str = "", experiment_id: Optional[int] = None,
                step_id: Optional[int] = None) -> dict:
    return _put(f"/api/boxes/{box_id}/slots/{position}", {
        "sample_name": sample_name, "notes": notes,
        "experiment_id": experiment_id, "step_id": step_id,
    })


def clear_slot(box_id: int, position: str) -> None:
    _delete(f"/api/boxes/{box_id}/slots/{position}")


# ─────────────────────────────────────────────
# Storage items
# ─────────────────────────────────────────────

def get_storage_items(exp_id: int) -> list[dict]:
    return _get(f"/api/experiments/{exp_id}/storage")


def create_storage_item(exp_id: int, item_label: str, tube_type: str = "",
                        notes_template: str = "", default_box: str = "") -> dict:
    return _post(f"/api/experiments/{exp_id}/storage", {
        "item_label": item_label,
        "tube_type": tube_type,
        "notes_template": notes_template,
        "default_box": default_box,
    })


def register_storage_item(exp_id: int, item_id: int, box_id: int,
                           row_label: str, col_label: str,
                           notes: str = "") -> dict:
    return _post(f"/api/experiments/{exp_id}/storage/register", {
        "item_id": item_id, "box_id": box_id,
        "row_label": row_label, "col_label": col_label, "notes": notes,
    })


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

def get_report(exp_id: int) -> str:
    result = _get(f"/api/experiments/{exp_id}/report")
    return result.get("markdown", "")


def save_report(exp_id: int) -> dict:
    return _post(f"/api/experiments/{exp_id}/report/save")
