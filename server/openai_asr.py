"""OpenAI speech-to-text adapter for ELN voice notes."""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import urllib.error
import urllib.request

from utils.app_settings import get_transcription_config


class OpenAiAsrError(RuntimeError):
    pass


def configured() -> bool:
    cfg = get_transcription_config()
    return cfg.get("provider") == "openai" and bool(cfg.get("openai_api_key"))


def _mime_type(path: str) -> str:
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _multipart(fields: dict[str, str], file_field: str, path: str) -> tuple[bytes, str]:
    boundary = "----eln-openai-" + secrets.token_hex(12)
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    filename = os.path.basename(path)
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: {_mime_type(path)}\r\n\r\n"
        ).encode("utf-8")
    )
    with open(path, "rb") as f:
        chunks.append(f.read())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def transcribe_file(path: str) -> str:
    cfg = get_transcription_config()
    if cfg.get("provider") != "openai":
        raise OpenAiAsrError("OpenAI ASR is not selected")
    api_key = cfg.get("openai_api_key", "")
    if not api_key:
        raise OpenAiAsrError("OpenAI API key is not configured")
    if not os.path.exists(path):
        raise OpenAiAsrError(f"Audio file not found: {path}")

    base_url = (cfg.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
    model = cfg.get("openai_model") or "gpt-4o-mini-transcribe"
    body, boundary = _multipart(
        {"model": model, "response_format": "json"},
        "file",
        path,
    )
    req = urllib.request.Request(
        base_url + "/audio/transcriptions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise OpenAiAsrError(f"OpenAI ASR HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise OpenAiAsrError(f"OpenAI ASR request failed: {exc}") from exc

    text = (data.get("text") or "").strip()
    return text
