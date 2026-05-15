"""
ELN App — Notifications
Cross-platform system notifications + alert sound.
Called when a countdown timer reaches zero.
"""

from __future__ import annotations
import logging
import os
import platform
import threading

logger = logging.getLogger(__name__)

# Path to bundled alert sound
_ASSET_DIR = os.path.join(os.path.dirname(__file__), "assets")
_ALERT_SOUND = os.path.join(_ASSET_DIR, "alert.mp3")
_ALERT_WAV = os.path.join(_ASSET_DIR, "alert.wav")
_alert_stop_event = threading.Event()


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def notify_timer_finished(step_title: str, experiment_name: str) -> None:
    """
    Fire system notification + play alert sound.
    Runs in a background thread so it never blocks the UI.
    """
    title = "计时结束"
    body = f"{experiment_name} · {step_title}"
    _alert_stop_event.clear()
    threading.Thread(
        target=_send_notification,
        args=(title, body),
        daemon=True,
    ).start()
    threading.Thread(
        target=_play_sound_loop,
        daemon=True,
    ).start()


def stop_alert_sound() -> None:
    """Stop any looping timer alert as soon as practical."""
    _alert_stop_event.set()
    if platform.system() == "Windows":
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass


# ─────────────────────────────────────────────
# Platform-specific notification
# ─────────────────────────────────────────────

def _send_notification(title: str, body: str) -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            _notify_macos(title, body)
        elif system == "Windows":
            _notify_windows(title, body)
        elif system == "Linux":
            _notify_linux(title, body)
        else:
            # iOS fallback
            _notify_flet(title, body)
    except Exception as e:
        logger.warning(f"Notification failed: {e}")


def _notify_macos(title: str, body: str) -> None:
    import subprocess
    script = (
        f'display notification "{body}" with title "{title}" '
        f'sound name "Glass"'
    )
    subprocess.run(["osascript", "-e", script], check=False)


def _notify_windows(title: str, body: str) -> None:
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(title, body, duration=5, threaded=True)
    except ImportError:
        # Fallback: Windows balloon via plyer
        _notify_plyer(title, body)


def _notify_plyer(title: str, body: str) -> None:
    try:
        from plyer import notification
        notification.notify(title=title, message=body, timeout=5)
    except Exception as e:
        logger.warning(f"plyer notification failed: {e}")


def _notify_linux(title: str, body: str) -> None:
    """Try notify-send (Linux desktop), then fall back to logging."""
    import subprocess
    try:
        subprocess.run(["notify-send", title, body], check=False, timeout=3)
        return
    except (FileNotFoundError, Exception):
        pass
    logger.info(f"[Notification] {title}: {body}")


def _notify_flet(title: str, body: str) -> None:
    """
    On iOS, Flet exposes page.show_snack_bar / local notifications.
    This is a best-effort fallback; the UI layer should also handle
    the timer_finished callback directly for in-app visual feedback.
    """
    _notify_linux(title, body)


# ─────────────────────────────────────────────
# Alert sound
# ─────────────────────────────────────────────

def _play_sound_loop() -> None:
    # The WAV is about 8 seconds. Loop for up to 10 minutes unless confirmed.
    for _ in range(75):
        if _alert_stop_event.is_set():
            break
        _play_sound()
        if _alert_stop_event.wait(0.2):
            break


def _play_sound() -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            _play_macos()
        elif system == "Windows":
            _play_windows()
        else:
            _play_pygame()
    except Exception as e:
        logger.warning(f"Sound playback failed: {e}")


def _play_macos() -> None:
    import subprocess
    if os.path.exists(_ALERT_SOUND):
        subprocess.run(["afplay", _ALERT_SOUND], check=False)
    else:
        # Built-in system sound fallback
        subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)


def _play_windows() -> None:
    try:
        import winsound
        if os.path.exists(_ALERT_WAV) and os.path.getsize(_ALERT_WAV) > 1024:
            winsound.PlaySound(
                _ALERT_WAV,
                winsound.SND_FILENAME | winsound.SND_ASYNC,
            )
            return
        if os.path.exists(_ALERT_SOUND) and os.path.getsize(_ALERT_SOUND) > 1024:
            try:
                import pygame
                pygame.mixer.init()
                pygame.mixer.music.load(_ALERT_SOUND)
                pygame.mixer.music.play()
                import time
                for _ in range(100):
                    if not pygame.mixer.music.get_busy():
                        break
                    time.sleep(0.1)
                return
            except Exception as e:
                logger.warning(f"MP3 alert failed, using Windows beep: {e}")
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        try:
            winsound.Beep(880, 700)
        except Exception:
            pass
    except ImportError:
        _play_pygame()


def _play_pygame() -> None:
    try:
        import pygame
        pygame.mixer.init()
        if os.path.exists(_ALERT_SOUND):
            pygame.mixer.music.load(_ALERT_SOUND)
            pygame.mixer.music.play()
            # Wait for playback to finish (max 10s)
            import time
            for _ in range(100):
                if not pygame.mixer.music.get_busy():
                    break
                time.sleep(0.1)
        else:
            logger.warning(f"Alert sound not found: {_ALERT_SOUND}")
    except ImportError:
        logger.warning("pygame not available for sound playback")
