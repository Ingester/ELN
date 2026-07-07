"""Audio post-processing helpers.

Phone MediaRecorder produces *fragmented* MP4 (ftyp/moov/moof/mdat) whose tiny
moov carries no full-file duration table. Plain <audio> players stall on those
after a few seconds. Remuxing to a normal faststart MP4 (moov at front, full
sample table) fixes playback and also gives ASR a cleaner file.

ffmpeg is provided by the `imageio-ffmpeg` package (no system install needed).
Everything degrades gracefully: if ffmpeg is missing, files are left untouched.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile


def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def remux_to_faststart_mp4(path: str) -> bool:
    """Rewrite an MP4/M4A in place as a plain faststart MP4. No-op (returns
    False) if the file isn't mp4-like or ffmpeg is unavailable."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".m4a", ".mp4", ".mov", ".aac"):
        return False
    ff = ffmpeg_exe()
    if not ff or not os.path.exists(path):
        return False
    tmp = path + ".remux.m4a"
    try:
        proc = subprocess.run(
            [ff, "-y", "-v", "error", "-i", path, "-c", "copy",
             "-movflags", "+faststart", tmp],
            capture_output=True, timeout=120,
        )
        if proc.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
            return True
    except Exception as exc:
        print(f"[audio] remux failed for {path}: {exc}")
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return False


def split_audio(path: str, seconds: int = 55) -> list[str]:
    """Split an audio file into <=`seconds` chunks (for the 60s-capped 一句话识别).
    Returns ordered chunk paths in a temp dir, or [path] if ffmpeg is missing or
    splitting fails. Clean up the returned dir with cleanup_chunks()."""
    ff = ffmpeg_exe()
    if not ff or not os.path.exists(path):
        return [path]
    ext = os.path.splitext(path)[1] or ".m4a"
    tmpdir = tempfile.mkdtemp(prefix="eln_asr_")
    pattern = os.path.join(tmpdir, "chunk_%03d" + ext)
    try:
        proc = subprocess.run(
            [ff, "-y", "-v", "error", "-i", path, "-f", "segment",
             "-segment_time", str(seconds), "-c", "copy", "-reset_timestamps", "1", pattern],
            capture_output=True, timeout=180,
        )
        chunks = sorted(glob.glob(os.path.join(tmpdir, "chunk_*" + ext)))
        if proc.returncode == 0 and chunks:
            return chunks
        print(f"[audio] split produced no chunks for {path}: {proc.stderr.decode('utf-8','replace')[:200]}")
    except Exception as exc:
        print(f"[audio] split failed for {path}: {exc}")
    shutil.rmtree(tmpdir, ignore_errors=True)
    return [path]


def cleanup_chunks(chunks: list[str]) -> None:
    """Remove the temp dir created by split_audio (no-op if chunks is [original])."""
    for c in chunks:
        d = os.path.dirname(c)
        if os.path.basename(d).startswith("eln_asr_"):
            shutil.rmtree(d, ignore_errors=True)
            return
