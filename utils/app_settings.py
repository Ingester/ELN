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
