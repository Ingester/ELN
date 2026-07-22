"""
Run the ELN app as native browser pages (FastAPI on port 8600).

By default this runs ONLY the FastAPI server that serves the mobile-friendly
native pages (/capture, /run, /history, /more, …). The old Flet desktop shell
(port 8550) is a parallel copy of the same UI and is no longer started — set
ELN_START_FLET=1 to bring it back if you ever need it.
"""

from __future__ import annotations

import os
import sys


def _load_persisted_windows_password() -> None:
    """Prefer the current user's saved ELN password over a stale parent env."""
    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "ELN_AUTH_PASSWORD")
        if str(value):
            os.environ["ELN_AUTH_PASSWORD"] = str(value)
    except (FileNotFoundError, OSError):
        pass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


if __name__ == "__main__":
    _load_persisted_windows_password()
    api_host = os.environ.get("ELN_API_HOST", "0.0.0.0")
    api_port = _env_int("ELN_API_PORT", 8600)

    os.environ["ELN_WEB_MODE"] = "1"
    os.environ["ELN_DYNAMIC_PUBLIC_URL"] = "1"
    # Native-only: all in-app links resolve to the 8600 pages (no 8550 Flet links).
    os.environ.setdefault("ELN_NATIVE_ONLY", "1")

    try:
        from server.startup import get_local_ip
        lan_ip = get_local_ip()
    except Exception:
        lan_ip = "127.0.0.1"

    if os.environ.get("ELN_START_FLET") == "1":
        # Legacy path: run the API in a background thread + the Flet shell on 8550.
        import flet as ft
        from main import main
        from server.startup import start_server

        os.environ.pop("ELN_NATIVE_ONLY", None)
        web_host = os.environ.get("ELN_WEB_HOST", "0.0.0.0")
        web_port = _env_int("ELN_WEB_PORT", 8550)
        os.environ["FLET_FORCE_WEB_SERVER"] = "1"
        os.environ["FLET_DISPLAY_URL_PREFIX"] = "ELN Web URL:"
        try:
            start_server(host=api_host, port=api_port)
        except Exception as exc:
            print(f"API server start failed: {exc}", file=sys.stderr)
        print(f"Starting ELN API at http://{api_host}:{api_port}")
        print(f"Starting Flet shell at http://{web_host}:{web_port}")
        ft.run(main, host=web_host, port=web_port,
               view=ft.AppView.FLET_APP_WEB, assets_dir="assets")
    else:
        # Default: run the native web server in the foreground (keeps the process
        # alive without Flet).
        from server.startup import run_foreground

        print(f"Starting ELN (native web) at http://{api_host}:{api_port}")
        print(f"iPhone LAN URL: http://{lan_ip}:{api_port}")
        run_foreground(host=api_host, port=api_port)
