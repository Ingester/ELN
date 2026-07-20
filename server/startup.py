"""
ELN App — Server Startup
Launches FastAPI + Uvicorn in a background thread on Windows.
Also handles graceful shutdown.
"""

from __future__ import annotations
import threading
import logging
import sys
import os
import socket

logger = logging.getLogger(__name__)

_server_thread: threading.Thread | None = None
_uvicorn_server = None


def get_api_port() -> int:
    """ELN API port. Default moved off 8000 (occupied by WSL claude-science
    under mirrored networking) to 8600."""
    try:
        return int(os.environ.get("ELN_API_PORT", "8600"))
    except ValueError:
        return 8600


def start_server(host: str = "0.0.0.0", port: int = None) -> None:
    """Start FastAPI server in a daemon background thread."""
    global _server_thread, _uvicorn_server

    if port is None:
        port = get_api_port()

    if _server_thread is not None and _server_thread.is_alive():
        return
    if _is_port_open("127.0.0.1", port):
        logger.info(f"ELN API server already available on port {port}")
        return

    # Initialize DB before starting server
    import db.database as db_ops
    db_ops.init_db()

    try:
        import uvicorn
        from server.api import app, mount_photos
        mount_photos(app)
    except ImportError:
        logger.error("uvicorn not installed. Run: pip install uvicorn[standard]")
        return

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    _uvicorn_server = uvicorn.Server(config)

    def _run():
        _uvicorn_server.run()

    _server_thread = threading.Thread(target=_run, daemon=True, name="eln-api-server")
    _server_thread.start()
    logger.info(f"ELN API server started on http://{host}:{port}")

    # Voice-note transcription worker (no-op unless faster-whisper is installed)
    try:
        from server.voice import notify_new_audio
        notify_new_audio()
    except Exception as exc:
        logger.warning(f"voice worker not started: {exc}")


def run_foreground(host: str = "0.0.0.0", port: int = None) -> None:
    """Run the API server in the FOREGROUND (blocking). Used for native-only web
    mode where there is no Flet shell to keep the process alive."""
    if port is None:
        port = get_api_port()

    import db.database as db_ops
    db_ops.init_db()

    import uvicorn
    from server.api import app, mount_photos
    mount_photos(app)

    try:
        from server.voice import notify_new_audio
        notify_new_audio()
    except Exception as exc:
        logger.warning(f"voice worker not started: {exc}")

    logger.info(f"ELN API server (foreground) on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


def stop_server() -> None:
    """Signal uvicorn to shut down."""
    global _uvicorn_server
    if _uvicorn_server is not None:
        _uvicorn_server.should_exit = True
        logger.info("ELN API server stopping...")


def get_local_ip() -> str:
    """Return the machine's LAN IP address for display in settings."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def is_server_running() -> bool:
    return (
        (_server_thread is not None and _server_thread.is_alive())
        or _is_port_open("127.0.0.1", get_api_port())
    )


def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False
