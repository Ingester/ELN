"""Audio post-processing helpers.

Phone MediaRecorder produces *fragmented* MP4 (ftyp/moov/moof/mdat) whose tiny
moov carries no full-file duration table. Plain <audio> players stall on those
after a few seconds. Remuxing to a normal faststart MP4 (moov at front, full
sample table) fixes playback and also gives ASR a cleaner file.

ffmpeg is provided by the `imageio-ffmpeg` package (no system install needed).
Everything degrades gracefully: if ffmpeg is missing, files are left untouched.
"""

from __future__ import annotations

import os
import subprocess


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
