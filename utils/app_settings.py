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
