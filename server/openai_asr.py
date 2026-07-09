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


# Plain vocabulary of terms likely to appear in these notes (中英夹杂 bio-research).
# Just words that may be spoken — not misheard->correct mappings. Used as the
# transcription `prompt` to bias spelling; override via settings openai_prompt.
DEFAULT_HOTWORDS = (
    "生物科研实验室的中英夹杂语音笔记（秀丽隐杆线虫 C. elegans、细胞培养、分子克隆、蛋白纯化）。"
    "英文术语："
    "C8orf82, ETF, ETFA, TMEM161, TMX2, APOL3, HRG-1, GST-4, FAT-7, FASN, APOE4, APP, Tau, "
    "mCherry, mEmerald, mScarlet, mEmerald-Mito-7, mito-7, GFP, pHAGE, LipoD293, "
    "N2, L4, gk, gk609478, 161, null, hermaphrodite, male, F1, F2, cross, "
    "CRISPR, sgRNA, RNAi, shRNA, knockout, KO, PCR, single-worm PCR, Co-IP, IP-MS, lysis, "
    "lentivirus, miniprep, transfection, Oil Red O, lipid droplet, colocalization, "
    "SEC, AKTA, IEX, DCIP, Strep-bead, OP50, brood size, coding sequence, primer, heme plate, "
    "ampicillin, Dox, doxycycline, HEK293, 293, T25。"
    "中文术语："
    "溶酶体、线粒体、脂滴、质粒、转染、转化、过表达、敲低、敲除、裂解、杂交、雄虫、雌雄同体、杂合体、"
    "单菌落、涂板、划线、摇菌、荧光、共定位、测序、引物、胶回收、注射、复苏、抗性、氨苄、对照、阴性对照、"
    "氧化应激、换液、加药、传代、传细胞、密度、后代、包病毒、梯度、24孔板、96孔板、6cm皿。"
)


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
    model = cfg.get("openai_model") or "gpt-4o-transcribe"
    fields = {"model": model, "response_format": "json"}
    # Hotword / context prompt biases spelling of domain terms (中英夹杂 bio notes).
    # Falls back to the built-in vocabulary when settings don't override it.
    prompt = (cfg.get("openai_prompt") or "").strip() or DEFAULT_HOTWORDS
    if prompt:
        fields["prompt"] = prompt
    # No language is forced, so Chinese-English code-switching is preserved.
    body, boundary = _multipart(fields, "file", path)
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
