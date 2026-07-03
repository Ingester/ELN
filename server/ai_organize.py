"""
ELN App — AI organizer for voice notes (语音速记 → 结构化实验记录).

Takes the free-form things you said while running an experiment and asks an LLM
to (a) rewrite them into clean per-step notes and (b) suggest values for the
step's data fields. It returns a *draft* — nothing is written to the record here;
the web UI shows the draft for you to confirm.

Provider-agnostic and dependency-free (stdlib urllib only, matching the rest of
the app):
  - provider "claude"  → Anthropic Messages API (x-api-key, /v1/messages)
  - provider "openai"  → OpenAI-compatible /chat/completions (also covers any
                          compatible gateway via a custom base_url)

Configure in Settings (stored in ELN_Data/settings.json under "ai").
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Optional

import db.database as db_ops
from utils.app_settings import get_ai_config

STEP_NOTES_KEY = "__eln_step_notes"

_SYSTEM_PROMPT = """你是一名严谨的实验室记录助手。用户在做实验时用语音随口记录了一些观察和数据（可能口语化、零散、有口误）。
给你这个实验的步骤结构（每步有标题、说明、以及需要记录的字段），以及用户的语音速记文本。
你的任务：把这些口语内容整理成规范的实验记录，并对应到正确的步骤。

严格返回 JSON（不要 markdown、不要解释），格式：
{
  "steps": [
    {
      "step_id": <整数，必须是给定步骤里的 id>,
      "note": "整理后的这一步备注（简洁、书面、保留关键观察与异常；没有就留空字符串）",
      "fields": [
        {"key": "<字段key，必须来自该步骤的字段列表>", "value": "<推断出的取值>", "reason": "<一句话依据，引用用户原话>"}
      ]
    }
  ],
  "unassigned": "<无法明确归入任何步骤的内容，原样保留；没有就留空字符串>"
}

规则：
- 只在用户确实提到某个字段的取值时才输出该字段；不要编造数字。
- 数值带单位时，字段值只填数字部分（除非字段本身要求单位）。
- note 用中文书面语，忠实于用户说的内容，不要添加用户没说的结论。
- 每个 step_id 最多出现一次；把同一步的多条观察合并进一个 note。
- 如果完全无法整理，steps 返回空数组，unassigned 放原文。"""


def ai_available() -> bool:
    return bool(get_ai_config().get("api_key"))


def _build_user_prompt(steps: list[dict], notes: list[str]) -> str:
    lines = ["## 实验步骤结构", ""]
    for s in steps:
        lines.append(f"- step_id={s['id']} · 第{s['step_index'] + 1}步：{s['title']}")
        desc = (s.get("description") or "").strip().replace("\n", " ")
        if desc:
            lines.append(f"    说明：{desc[:300]}")
        fields = s.get("fields") or []
        if fields:
            fdesc = "；".join(
                f"{f.get('key')}({f.get('label') or f.get('key')}"
                + (f"，类型{f.get('type')}" if f.get('type') else "")
                + (f"，选项:{'/'.join(f.get('options'))}" if f.get('options') else "")
                + ")"
                for f in fields
            )
            lines.append(f"    可记录字段：{fdesc}")
    lines.append("")
    lines.append("## 用户的语音速记（按时间顺序）")
    lines.append("")
    for i, n in enumerate(notes, 1):
        lines.append(f"{i}. {n}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # be forgiving: grab the outermost {...}
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def _call_claude(cfg: dict, system: str, user: str) -> str:
    url = (cfg.get("base_url") or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    payload = json.dumps({
        "model": cfg["model"],
        "max_tokens": 4096,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": cfg["api_key"],
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read())
    parts = [b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"]
    return "".join(parts)


def _call_openai(cfg: dict, system: str, user: str) -> str:
    base = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    url = base + "/chat/completions"
    payload = json.dumps({
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 4096,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {cfg['api_key']}",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read())
    return result["choices"][0]["message"]["content"]


def organize_experiment(exp_id: int,
                        note_texts: Optional[list[str]] = None) -> dict[str, Any]:
    """Return a review draft. Raises RuntimeError with a user-facing message on failure."""
    cfg = get_ai_config()
    if not cfg.get("api_key"):
        raise RuntimeError("还没有配置 AI。请在电脑端「设置 → AI 整理」里填入模型服务和密钥。")

    steps = db_ops.get_steps(exp_id)
    if not steps:
        raise RuntimeError("这个实验还没有步骤。")

    if note_texts is None:
        note_texts = [
            (n.get("text") or "").strip()
            for n in db_ops.list_voice_notes(exp_id)
            if (n.get("text") or "").strip()
        ]
    note_texts = [t for t in (note_texts or []) if t.strip()]
    if not note_texts:
        raise RuntimeError("没有可整理的语音速记（还没有转成文字的内容）。")

    step_dicts = []
    valid_field_keys: dict[int, set[str]] = {}
    for s in steps:
        fields = [
            {"key": f.key, "label": f.label, "type": f.type, "options": f.options}
            for f in s.get_fields()
        ]
        valid_field_keys[s.id] = {f["key"] for f in fields}
        step_dicts.append({
            "id": s.id, "step_index": s.step_index,
            "title": s.title, "description": s.description, "fields": fields,
        })

    user_prompt = _build_user_prompt(step_dicts, note_texts)
    try:
        raw = _call_claude(cfg, _SYSTEM_PROMPT, user_prompt) if cfg["provider"] == "claude" \
            else _call_openai(cfg, _SYSTEM_PROMPT, user_prompt)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300] if hasattr(e, "read") else str(e)
        raise RuntimeError(f"调用模型失败（HTTP {e.code}）：{detail}")
    except Exception as e:
        raise RuntimeError(f"调用模型失败：{e}")

    try:
        data = _extract_json(raw)
    except Exception:
        raise RuntimeError("模型返回的内容不是有效 JSON，无法整理。可稍后重试或换个模型。")

    # Validate + enrich against the real step/field structure.
    steps_by_id = {s["id"]: s for s in step_dicts}
    out_steps = []
    for item in data.get("steps", []) or []:
        try:
            sid = int(item.get("step_id"))
        except (TypeError, ValueError):
            continue
        s = steps_by_id.get(sid)
        if not s:
            continue
        note = str(item.get("note") or "").strip()
        raw_fields = item.get("fields") or []
        step_obj = next((x for x in steps if x.id == sid), None)
        current_vals = step_obj.get_values() if step_obj else {}
        fields_out = []
        for f in raw_fields:
            key = str(f.get("key") or "").strip()
            if key not in valid_field_keys.get(sid, set()):
                continue
            fields_out.append({
                "key": key,
                "label": next((ff["label"] for ff in s["fields"] if ff["key"] == key), key),
                "current": str(current_vals.get(key, "") or ""),
                "suggested": str(f.get("value") or ""),
                "reason": str(f.get("reason") or ""),
            })
        if note or fields_out:
            out_steps.append({
                "step_id": sid,
                "step_index": s["step_index"],
                "title": s["title"],
                "note": note,
                "fields": fields_out,
            })

    out_steps.sort(key=lambda x: x["step_index"])
    return {
        "experiment_id": exp_id,
        "provider": cfg["provider"],
        "model": cfg["model"],
        "steps": out_steps,
        "unassigned": str(data.get("unassigned") or "").strip(),
        "source_note_count": len(note_texts),
    }
