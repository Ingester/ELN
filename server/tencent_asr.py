"""Tencent Cloud ASR adapter for short ELN voice notes.

Uses the TencentCloud API 3.0 TC3-HMAC-SHA256 signing flow directly so the
ELN app does not need the optional tencentcloud-sdk-python package.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from utils.app_settings import get_transcription_config

SERVICE = "asr"
HOST = "asr.tencentcloudapi.com"
ENDPOINT = "https://" + HOST
VERSION = "2019-06-14"
ACTION = "SentenceRecognition"


class TencentAsrError(RuntimeError):
    pass


def configured() -> bool:
    cfg = get_transcription_config()
    return (
        cfg.get("provider") == "tencent"
        and bool(cfg.get("tencent_secret_id"))
        and bool(cfg.get("tencent_secret_key"))
    )


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _authorization(secret_id: str, secret_key: str, payload: str,
                   timestamp: int) -> str:
    algorithm = "TC3-HMAC-SHA256"
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{HOST}\n"
    signed_headers = "content-type;host"
    hashed_request_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = "\n".join([
        "POST",
        "/",
        "",
        canonical_headers,
        signed_headers,
        hashed_request_payload,
    ])
    credential_scope = f"{date}/{SERVICE}/tc3_request"
    hashed_canonical_request = hashlib.sha256(
        canonical_request.encode("utf-8")
    ).hexdigest()
    string_to_sign = "\n".join([
        algorithm,
        str(timestamp),
        credential_scope,
        hashed_canonical_request,
    ])
    secret_date = _sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _sign(secret_date, SERVICE)
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


def _voice_format(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in {"m4a", "mp3", "wav", "pcm", "ogg", "aac", "amr", "silk"}:
        return ext
    if ext == "webm":
        # Tencent ASR may reject webm in some regions. Keep the original format
        # name so the API error is honest instead of pretending it is m4a.
        return "webm"
    return ext or "m4a"


def _tc3_post(action: str, payload_obj: dict[str, Any], timeout: int = 60) -> dict:
    """Sign + POST one TencentCloud ASR API 3.0 action, return the Response dict."""
    cfg = get_transcription_config()
    secret_id = cfg.get("tencent_secret_id", "")
    secret_key = cfg.get("tencent_secret_key", "")
    if not (secret_id and secret_key):
        raise TencentAsrError("Tencent ASR key is not configured")
    payload = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":"))
    timestamp = int(time.time())
    req = urllib.request.Request(
        ENDPOINT,
        data=payload.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": _authorization(secret_id, secret_key, payload, timestamp),
            "Content-Type": "application/json; charset=utf-8",
            "Host": HOST,
            "X-TC-Action": action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": VERSION,
            "X-TC-Region": cfg.get("tencent_region") or "ap-shanghai",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise TencentAsrError(f"Tencent ASR HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise TencentAsrError(f"Tencent ASR request failed: {exc}") from exc
    response = data.get("Response") or {}
    if response.get("Error"):
        err = response["Error"]
        raise TencentAsrError(
            f"Tencent ASR {err.get('Code', 'Error')}: {err.get('Message', '')}"
        )
    return response


def transcribe_file(path: str) -> str:
    cfg = get_transcription_config()
    if cfg.get("provider") != "tencent":
        raise TencentAsrError("Tencent ASR is not selected")
    if not os.path.exists(path):
        raise TencentAsrError(f"Audio file not found: {path}")
    with open(path, "rb") as f:
        audio = f.read()
    if not audio:
        raise TencentAsrError("Audio file is empty")

    # Try 一句话识别 (fast, <=60s). If the clip is too long, fall back to the
    # 录音文件识别 (recording file recognition) async task, which handles hours.
    payload_obj: dict[str, Any] = {
        "ProjectId": 0,
        "SubServiceType": 2,
        "EngSerViceType": cfg.get("tencent_engine") or "16k_zh",
        "SourceType": 1,
        "VoiceFormat": _voice_format(path),
        "UsrAudioKey": os.path.basename(path)[:60],
        "Data": base64.b64encode(audio).decode("ascii"),
        "DataLen": len(audio),
    }
    try:
        response = _tc3_post(ACTION, payload_obj)
    except TencentAsrError as exc:
        if _is_too_long(exc):
            return _transcribe_rectask(path, audio, cfg)
        raise
    return (response.get("Result") or "").strip()


def _is_too_long(exc: Exception) -> bool:
    msg = str(exc)
    return "TooLong" in msg or "ErrorVoicedata" in msg or "exceeds" in msg or "60 seconds" in msg


def _transcribe_rectask(path: str, audio: bytes, cfg: dict) -> str:
    """Long audio via 录音文件识别 (CreateRecTask + DescribeTaskStatus polling).
    Reuses the same TC3 credentials — no extra config needed."""
    create = {
        "EngineModelType": cfg.get("tencent_engine") or "16k_zh",
        "ChannelNum": 1,
        "ResTextFormat": 0,
        "SourceType": 1,
        "Data": base64.b64encode(audio).decode("ascii"),
        "DataLen": len(audio),
    }
    resp = _tc3_post("CreateRecTask", create)
    task_id = (resp.get("Data") or {}).get("TaskId")
    if task_id is None:
        raise TencentAsrError("录音文件识别未返回 TaskId")
    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(4)
        status_resp = _tc3_post("DescribeTaskStatus", {"TaskId": int(task_id)})
        data = status_resp.get("Data") or {}
        status = data.get("Status")
        if status == 2:  # success
            return _clean_rectask_result(data.get("Result") or "")
        if status == 3:  # failed
            raise TencentAsrError(
                f"录音文件识别失败: {data.get('ErrorMsg') or data.get('Message') or ''}"
            )
    raise TencentAsrError("录音文件识别超时")


def _clean_rectask_result(text: str) -> str:
    """ResTextFormat=0 returns lines like '[0:0.240,0:3.100]  你好' — strip the
    leading timestamp bracket from each line and join."""
    parts = []
    for line in str(text).splitlines():
        line = re.sub(r"^\s*\[[^\]]*\]\s*", "", line).strip()
        if line:
            parts.append(line)
    joined = " ".join(parts).strip()
    return joined or str(text).strip()
