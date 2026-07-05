"""
ELN App — Voice note transcription worker (optional).

Audio voice notes uploaded from the phone are transcribed in a background
thread and the text lands in the experiment record automatically. Tencent Cloud
ASR or OpenAI speech-to-text can be configured for cloud transcription;
otherwise the worker falls back to local `faster-whisper` when installed.
Without any provider, notes stay as playable audio marked "待转写" — nothing is
lost.

Install (optional):
    python -m pip install faster-whisper
Model size via env ELN_WHISPER_MODEL (default: "small"; "base" is faster,
"medium" is more accurate).
"""

from __future__ import annotations

import os
import threading

import db.database as db_ops

_worker_lock = threading.Lock()
_worker_started = False
_wake = threading.Event()
_model = None
_model_failed = False


def transcription_available() -> bool:
    try:
        from server import openai_asr
        if openai_asr.configured():
            return True
    except Exception:
        pass
    try:
        from server import tencent_asr
        if tencent_asr.configured():
            return True
    except Exception:
        pass
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _get_model():
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model
    try:
        from faster_whisper import WhisperModel
        size = os.environ.get("ELN_WHISPER_MODEL", "small")
        _model = WhisperModel(size, device="cpu", compute_type="int8")
    except Exception as exc:
        print(f"[voice] faster-whisper unavailable: {exc}")
        _model_failed = True
    return _model


def _transcribe_file(path: str) -> str:
    try:
        from server import openai_asr
        if openai_asr.configured():
            return openai_asr.transcribe_file(path)
    except Exception as exc:
        print(f"[voice] OpenAI ASR failed, falling back if possible: {exc}")

    try:
        from server import tencent_asr
        if tencent_asr.configured():
            return tencent_asr.transcribe_file(path)
    except Exception as exc:
        print(f"[voice] Tencent ASR failed, falling back if possible: {exc}")

    model = _get_model()
    if model is None:
        raise RuntimeError("no transcription model")
    segments, _info = model.transcribe(
        path,
        language=os.environ.get("ELN_WHISPER_LANG") or None,
        vad_filter=True,
        beam_size=5,
    )
    return "".join(seg.text for seg in segments).strip()


def _worker_loop() -> None:
    while True:
        _wake.wait(timeout=300)
        _wake.clear()
        try:
            pending = db_ops.list_pending_voice_notes()
        except Exception:
            pending = []
        if pending and not transcription_available():
            # Mark as audio-only so the UI stops saying "转写中"
            for note in pending:
                try:
                    db_ops.update_voice_note(note["id"], status="audio_only")
                except Exception:
                    pass
            continue
        for note in pending:
            path = _resolve_audio_path(note["audio_path"])
            if not os.path.exists(path):
                db_ops.update_voice_note(note["id"], status="audio_only")
                continue
            try:
                text = _transcribe_file(path)
                db_ops.update_voice_note(
                    note["id"],
                    text=text or "(未识别到语音)",
                    status="done",
                )
                print(f"[voice] transcribed note {note['id']}: {len(text)} chars")
            except Exception as exc:
                print(f"[voice] transcription failed for note {note['id']}: {exc}")
                db_ops.update_voice_note(note["id"], status="audio_only")


def transcribe_path_once(path: str) -> str:
    """Transcribe a saved audio file synchronously for inbox captures."""
    return _transcribe_file(path)


def _resolve_audio_path(rel_path: str) -> str:
    rel = (rel_path or "").replace("/", os.sep)
    candidates = [
        os.path.join(db_ops.get_audio_dir(), rel),
        os.path.join(db_ops.get_photos_dir(), rel),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def ensure_worker() -> None:
    """Start the background transcription thread once (idempotent)."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, daemon=True, name="eln-voice").start()
        _worker_started = True


def notify_new_audio() -> None:
    """Wake the worker after a new audio note is uploaded."""
    ensure_worker()
    _wake.set()
