"""Small persistent app settings stored next to ELN data."""

from __future__ import annotations

import json
import os
from typing import Any


def _settings_path() -> str:
    root = os.path.join(os.path.expanduser("~"), "ELN_Data")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "settings.json")


def load_settings() -> dict[str, Any]:
    try:
        with open(_settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def save_settings(settings: dict[str, Any]) -> None:
    with open(_settings_path(), "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def get_language() -> str:
    lang = str(load_settings().get("language", "zh")).lower()
    return "en" if lang.startswith("en") else "zh"


def set_language(language: str) -> None:
    settings = load_settings()
    settings["language"] = "en" if str(language).lower().startswith("en") else "zh"
    save_settings(settings)


def is_english() -> bool:
    return get_language() == "en"


# ─────────────────────────────────────────────
# AI (语音速记整理) configuration
# ─────────────────────────────────────────────

_AI_DEFAULTS = {
    "provider": "claude",   # "claude" | "openai"
    "api_key": "",
    "base_url": "",         # optional custom endpoint (OpenAI-compatible)
    "model": "",            # empty → provider default
}


def get_ai_config() -> dict[str, str]:
    cfg = load_settings().get("ai", {})
    if not isinstance(cfg, dict):
        cfg = {}
    out = dict(_AI_DEFAULTS)
    for k in out:
        v = cfg.get(k)
        if isinstance(v, str):
            out[k] = v
    provider = out["provider"].lower()
    out["provider"] = "openai" if provider.startswith("openai") else "claude"
    if not out["model"]:
        out["model"] = "claude-opus-4-8" if out["provider"] == "claude" else "gpt-4o-mini"
    return out


def set_ai_config(provider: str = None, api_key: str = None,
                  base_url: str = None, model: str = None) -> None:
    settings = load_settings()
    cfg = settings.get("ai", {})
    if not isinstance(cfg, dict):
        cfg = {}
    if provider is not None:
        cfg["provider"] = "openai" if str(provider).lower().startswith("openai") else "claude"
    if api_key is not None:
        cfg["api_key"] = api_key
    if base_url is not None:
        cfg["base_url"] = base_url.strip()
    if model is not None:
        cfg["model"] = model.strip()
    settings["ai"] = cfg
    save_settings(settings)


def ai_configured() -> bool:
    return bool(get_ai_config().get("api_key"))


# ─────────────────────────────────────────────
# Speech-to-text configuration
# ─────────────────────────────────────────────

_TRANSCRIPTION_DEFAULTS = {
    "provider": "local",        # "local" | "tencent" | "openai"
    "tencent_secret_id": "",
    "tencent_secret_key": "",
    "tencent_region": "ap-shanghai",
    "tencent_engine": "16k_zh",
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model": "gpt-4o-mini-transcribe",
}


def get_transcription_config() -> dict[str, str]:
    cfg = load_settings().get("transcription", {})
    if not isinstance(cfg, dict):
        cfg = {}
    out = dict(_TRANSCRIPTION_DEFAULTS)
    for k in out:
        v = cfg.get(k)
        if isinstance(v, str):
            out[k] = v.strip()

    provider = os.environ.get("ELN_TRANSCRIBE_PROVIDER", out["provider"]).strip().lower()
    if provider.startswith("tencent"):
        out["provider"] = "tencent"
    elif provider.startswith("openai"):
        out["provider"] = "openai"
    else:
        out["provider"] = "local"
    out["tencent_secret_id"] = (
        os.environ.get("TENCENTCLOUD_SECRET_ID")
        or os.environ.get("TENCENT_SECRET_ID")
        or out["tencent_secret_id"]
    ).strip()
    out["tencent_secret_key"] = (
        os.environ.get("TENCENTCLOUD_SECRET_KEY")
        or os.environ.get("TENCENT_SECRET_KEY")
        or out["tencent_secret_key"]
    ).strip()
    out["tencent_region"] = (
        os.environ.get("TENCENTCLOUD_REGION")
        or os.environ.get("ELN_TENCENT_REGION")
        or out["tencent_region"]
    ).strip() or "ap-shanghai"
    out["tencent_engine"] = (
        os.environ.get("ELN_TENCENT_ASR_ENGINE")
        or out["tencent_engine"]
    ).strip() or "16k_zh"
    out["openai_api_key"] = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ELN_OPENAI_TRANSCRIBE_API_KEY")
        or out["openai_api_key"]
    ).strip()
    out["openai_base_url"] = (
        os.environ.get("ELN_OPENAI_TRANSCRIBE_BASE_URL")
        or out["openai_base_url"]
    ).strip() or "https://api.openai.com/v1"
    out["openai_model"] = (
        os.environ.get("ELN_OPENAI_TRANSCRIBE_MODEL")
        or out["openai_model"]
    ).strip() or "gpt-4o-mini-transcribe"
    return out


def set_transcription_config(provider: str = None, tencent_secret_id: str = None,
                             tencent_secret_key: str = None, tencent_region: str = None,
                             tencent_engine: str = None, openai_api_key: str = None,
                             openai_base_url: str = None, openai_model: str = None) -> None:
    settings = load_settings()
    cfg = settings.get("transcription", {})
    if not isinstance(cfg, dict):
        cfg = {}
    if provider is not None:
        p = str(provider).strip().lower()
        if p.startswith("tencent"):
            cfg["provider"] = "tencent"
        elif p.startswith("openai"):
            cfg["provider"] = "openai"
        else:
            cfg["provider"] = "local"
    if tencent_secret_id is not None:
        cfg["tencent_secret_id"] = tencent_secret_id.strip()
    if tencent_secret_key is not None:
        cfg["tencent_secret_key"] = tencent_secret_key.strip()
    if tencent_region is not None:
        cfg["tencent_region"] = tencent_region.strip()
    if tencent_engine is not None:
        cfg["tencent_engine"] = tencent_engine.strip()
    if openai_api_key is not None:
        cfg["openai_api_key"] = openai_api_key.strip()
    if openai_base_url is not None:
        cfg["openai_base_url"] = openai_base_url.strip()
    if openai_model is not None:
        cfg["openai_model"] = openai_model.strip()
    settings["transcription"] = cfg
    save_settings(settings)


def transcription_configured() -> bool:
    cfg = get_transcription_config()
    if cfg["provider"] == "tencent":
        return bool(cfg["tencent_secret_id"] and cfg["tencent_secret_key"])
    if cfg["provider"] == "openai":
        return bool(cfg["openai_api_key"])
    return True
