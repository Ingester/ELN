"""
ELN App — Protocol Parser
Parses pasted text (JSON or free-form) into a ProtocolDefinition.
Supports:
  1. Direct JSON parsing
  2. AI-assisted parsing via OpenAI-compatible API (optional)
  3. Basic heuristic parsing as fallback
"""

from __future__ import annotations
import json
import re
import logging
from typing import Optional

from db.models import ProtocolDefinition, ProtocolStep, ProtocolField, StorageItemTemplate

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────

def parse_protocol_text(text: str,
                        use_ai: bool = False,
                        ai_api_key: str = "",
                        ai_base_url: str = "") -> ProtocolDefinition:
    """
    Parse pasted text into a ProtocolDefinition.
    Tries JSON first, then AI (if enabled), then heuristic.
    """
    text = text.strip()

    # 1. Try direct JSON
    try:
        data = json.loads(text)
        return ProtocolDefinition.from_dict(data)
    except (json.JSONDecodeError, KeyError):
        pass

    # 2. Try AI parsing
    if use_ai and ai_api_key:
        try:
            return _parse_with_ai(text, ai_api_key, ai_base_url)
        except Exception as e:
            logger.warning(f"AI parsing failed: {e}, falling back to heuristic")

    # 3. Heuristic fallback
    return _parse_heuristic(text)


# ─────────────────────────────────────────────
# AI-assisted parsing
# ─────────────────────────────────────────────

_AI_SYSTEM_PROMPT = """You are a lab protocol parser. Convert the user's protocol text into a JSON object matching this exact schema:

{
  "protocol_name": "string",
  "version": "1.0",
  "author": "",
  "storage_items": [
    {"key": "string", "label": "string", "tube_type": "string", "default_box": "", "notes_template": ""}
  ],
  "steps": [
    {
      "title": "string",
      "description": "string",
      "timer_seconds": 0,
      "has_camera": false,
      "camera_required": false,
      "fields": [
        {"key": "string", "label": "string", "type": "text|number|dropdown",
         "default": "", "required": false, "options": []}
      ]
    }
  ]
}

Rules:
- timer_seconds: convert time mentions to seconds (e.g. "30 min" → 1800, "2h" → 7200)
- has_camera: true if step mentions gel, image, photo, picture, result
- storage_items: infer from mentions of tubes, samples, products to store
- fields: add for any variable quantities (volumes, temperatures, counts)
- Return ONLY valid JSON, no markdown, no explanation."""


def _parse_with_ai(text: str, api_key: str, base_url: str = "") -> ProtocolDefinition:
    """Call OpenAI-compatible API to parse protocol text."""
    import urllib.request
    import urllib.error

    url = (base_url.rstrip("/") + "/chat/completions") if base_url else \
          "https://api.openai.com/v1/chat/completions"

    payload = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": _AI_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "max_tokens": 4096,
    }).encode()

    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    content = result["choices"][0]["message"]["content"].strip()
    # Strip markdown code fences if present
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    data = json.loads(content)
    return ProtocolDefinition.from_dict(data)


# ─────────────────────────────────────────────
# Heuristic parser
# ─────────────────────────────────────────────

def _parse_heuristic(text: str) -> ProtocolDefinition:
    """
    Best-effort heuristic parser for free-form protocol text.
    Splits on numbered steps, extracts timers and camera hints.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Try to extract protocol name from first line
    protocol_name = lines[0] if lines else "Imported Protocol"
    # Remove common prefixes
    protocol_name = re.sub(r"^(protocol|实验|步骤|procedure)[:\s]*", "",
                           protocol_name, flags=re.IGNORECASE).strip()
    if not protocol_name:
        protocol_name = "Imported Protocol"

    # Split into step blocks
    step_blocks = _split_into_steps(lines[1:] if len(lines) > 1 else lines)

    steps = []
    for block in step_blocks:
        step = _parse_step_block(block)
        if step:
            steps.append(step)

    if not steps:
        # Treat entire text as a single step
        steps = [ProtocolStep(
            title="Step 1",
            description=text[:500],
            timer_seconds=0,
        )]

    return ProtocolDefinition(
        protocol_name=protocol_name,
        steps=steps,
    )


def _split_into_steps(lines: list[str]) -> list[list[str]]:
    """Split lines into blocks, each starting with a step number or bullet."""
    step_re = re.compile(r"^(\d+[\.\)、]|Step\s*\d+|步骤\s*\d+)", re.IGNORECASE)
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if step_re.match(line):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)

    if current:
        blocks.append(current)

    return blocks if blocks else [lines]


def _parse_step_block(block: list[str]) -> Optional[ProtocolStep]:
    if not block:
        return None

    title_line = block[0]
    # Clean step number prefix from title
    title = re.sub(r"^(\d+[\.\)、]|Step\s*\d+[:\s]*|步骤\s*\d+[:\s]*)", "",
                   title_line, flags=re.IGNORECASE).strip()
    if not title:
        title = title_line

    description = " ".join(block[1:]) if len(block) > 1 else title_line

    # Extract timer
    timer_seconds = _extract_timer(description) or _extract_timer(title_line)

    # Detect camera hint
    camera_keywords = ["gel", "image", "photo", "picture", "result",
                       "凝胶", "拍照", "图片", "结果", "电泳"]
    has_camera = any(kw.lower() in description.lower() for kw in camera_keywords)

    return ProtocolStep(
        title=title[:100],
        description=description,
        timer_seconds=timer_seconds or 0,
        has_camera=has_camera,
        camera_required=False,
    )


def _extract_timer(text: str) -> Optional[int]:
    """Extract timer duration in seconds from text."""
    # Patterns: "30 min", "2h", "90 seconds", "1.5 hours", "30分钟"
    patterns = [
        (r"(\d+(?:\.\d+)?)\s*(?:hours?|hr|h)\b", 3600),
        (r"(\d+(?:\.\d+)?)\s*(?:minutes?|min|分钟|分)\b", 60),
        (r"(\d+(?:\.\d+)?)\s*(?:seconds?|sec|s)\b", 1),
    ]
    for pattern, multiplier in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return int(float(m.group(1)) * multiplier)
    return None


# ─────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────

def validate_protocol(definition: ProtocolDefinition) -> list[str]:
    """
    Return list of validation warnings (not errors — protocol is still usable).
    """
    warnings = []
    if not definition.protocol_name:
        warnings.append("Protocol name is empty")
    if not definition.steps:
        warnings.append("Protocol has no steps")
    for i, step in enumerate(definition.steps):
        if not step.title:
            warnings.append(f"Step {i+1} has no title")
        if step.timer_seconds < 0:
            warnings.append(f"Step {i+1} has negative timer")
        for f in step.fields:
            if not f.key:
                warnings.append(f"Step {i+1} field missing key")
            if f.type == "dropdown" and not f.options:
                warnings.append(f"Step {i+1} dropdown field '{f.label}' has no options")
    return warnings
