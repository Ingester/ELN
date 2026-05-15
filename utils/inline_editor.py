"""
ELN App — Inline Editor
Parses step description text to find editable numbers (with lab units).
Returns a list of segments that the UI renders as plain text or orange-underlined spans.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Union

# Lab units to recognise (order matters: longer first to avoid partial matches)
_LAB_UNITS = [
    r"ng/µL", r"ng/uL", r"ng/μL",
    r"U/µL", r"U/uL",
    # Volume
    r"µL", r"uL", r"μL", r"mL", r"L",
    # Temperature
    r"°C", r"℃",
    # Time
    r"分钟", r"秒", r"小时", r"min", r"sec", r"s\b", r"h\b", r"hr",
    # Mass / concentration
    r"ng", r"µg", r"ug", r"μg", r"mg", r"g\b",
    r"nM", r"µM", r"uM", r"μM", r"mM", r"M\b",
    # Cycles / counts
    r"循环", r"cycles?", r"x\b", r"×",
    # Percentage
    r"%",
    # Voltage
    r"V",
    # rpm
    r"rpm", r"rcf", r"g\b",
    # bp / kb
    r"bp", r"kb",
]

# Build regex: number (int or float) optionally followed by a space and a unit
_UNIT_PATTERN = "|".join(_LAB_UNITS)
_NUMBER_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(" + _UNIT_PATTERN + r")?",
    re.UNICODE,
)

# Also match standalone integers that look like counts (≥2 digits, no unit)
# e.g. "30 cycles", "8 colonies" — we capture the number before a word
_STANDALONE_RE = re.compile(r"\b(\d{1,4})\b")


@dataclass
class TextSegment:
    """Plain text segment."""
    text: str
    is_editable: bool = False
    number_value: str = ""      # original number string (e.g. "25")
    unit: str = ""              # unit string (e.g. "µL"), empty for standalone
    override_key: str = ""      # key used in description_overrides_json


def parse_description(description: str,
                      overrides: dict[str, str] | None = None) -> list[TextSegment]:
    """
    Parse a step description into a list of TextSegments.
    Numbers with lab units become editable spans.
    overrides: {override_key: new_value_str} — applied before rendering.
    """
    if overrides is None:
        overrides = {}

    segments: list[TextSegment] = []
    pos = 0

    for m in _NUMBER_RE.finditer(description):
        start, end = m.start(), m.end()
        number_str = m.group(1)
        unit_str = m.group(2) or ""

        # Text before this match
        if start > pos:
            segments.append(TextSegment(text=description[pos:start]))

        # Build override key: "25µL" → key "25µL"
        raw_key = f"{number_str}{unit_str}"
        # Apply override if present
        display_value = overrides.get(raw_key, number_str)

        segments.append(TextSegment(
            text=f"{display_value}{unit_str}",
            is_editable=True,
            number_value=number_str,
            unit=unit_str,
            override_key=raw_key,
        ))
        pos = end

    # Remaining text
    if pos < len(description):
        segments.append(TextSegment(text=description[pos:]))

    return segments


def apply_override(description: str, override_key: str,
                   new_value: str,
                   existing_overrides: dict[str, str]) -> dict[str, str]:
    """
    Return updated overrides dict with new_value set for override_key.
    Validates that new_value is a valid number.
    """
    try:
        float(new_value)
    except ValueError:
        raise ValueError(f"'{new_value}' is not a valid number")
    updated = dict(existing_overrides)
    updated[override_key] = new_value
    return updated


def render_plain(description: str, overrides: dict[str, str] | None = None) -> str:
    """
    Return description with overrides applied as plain text.
    Used for report generation.
    """
    if not overrides:
        return description
    segments = parse_description(description, overrides)
    return "".join(s.text for s in segments)


def get_editable_numbers(description: str) -> list[dict]:
    """
    Return list of {override_key, number_value, unit} for all editable numbers.
    Used by the editor UI to build the edit form.
    """
    segments = parse_description(description)
    return [
        {
            "override_key": s.override_key,
            "number_value": s.number_value,
            "unit": s.unit,
        }
        for s in segments if s.is_editable
    ]
