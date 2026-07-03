"""
Run ELN App in a browser instead of the Flet desktop shell.

This avoids the Windows WebView2/Flet desktop renderer that was causing
display blackouts on this machine.
"""

from __future__ import annotations

import os
import sys

import flet as ft

from main import main


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


if __name__ == "__main__":
    host = os.environ.get("ELN_WEB_HOST", "0.0.0.0")
    port = _env_int("ELN_WEB_PORT", 8550)
    api_host = os.environ.get("ELN_API_HOST", "0.0.0.0")
    api_port = _env_int("ELN_API_PORT", 8000)

    os.environ["ELN_WEB_MODE"] = "1"
    os.environ["ELN_DYNAMIC_PUBLIC_URL"] = "1"
    open_browser = os.environ.get("ELN_WEB_OPEN", "0") == "1"
    if not open_browser:
        os.environ["FLET_FORCE_WEB_SERVER"] = "1"
        os.environ["FLET_DISPLAY_URL_PREFIX"] = "ELN Web URL:"

    try:
        from server.startup import get_local_ip
        lan_ip = get_local_ip()
    except Exception:
        lan_ip = "127.0.0.1"

    try:
        from server.startup import start_server
        start_server(host=api_host, port=api_port)
    except Exception as exc:
        print(f"API server start failed: {exc}", file=sys.stderr)

    print(f"Starting ELN Web at http://{host}:{port}")
    print(f"Starting ELN API at http://{api_host}:{api_port}")
    print(f"iPhone LAN URL: http://{lan_ip}:{port}")
    print("Public URLs are resolved dynamically; restart after switching networks if the current page is already open.")
    print("Set ELN_WEB_OPEN=1 if you want it to open the browser automatically.")

    try:
        ft.run(
            main,
            host=host,
            port=port,
            view=ft.AppView.FLET_APP_WEB,
            assets_dir="assets",
        )
    except ModuleNotFoundError as exc:
        if exc.name == "flet_web":
            print(
                "ELN Web mode requires the flet-web package.\n"
                "Install it in py310 with:\n"
                "  python -m pip install flet-web==0.85.0",
                file=sys.stderr,
            )
            raise SystemExit(2)
        raise
