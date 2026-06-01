"""
ELN App — Main Entry Point
Platform detection → local mode (Windows/Mac) or remote mode (iOS).
Navigation framework with 4-tab bottom nav (mobile) or left rail (desktop).
"""

from __future__ import annotations
import json
import platform
import sys
import os
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
import flet as ft
from utils.i18n import tr

def _open_overlay(page, ctrl):
    """Open a dialog/snackbar compatible with flet 0.70+."""
    if ctrl not in page.overlay:
        page.overlay.append(ctrl)
    ctrl.open = True
    page.update()

def _close_overlay(page, ctrl):
    """Close a dialog/snackbar compatible with flet 0.70+."""
    ctrl.open = False
    page.update()


def _debug_log(message: str) -> None:
    try:
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "ui_debug.log"), "a", encoding="utf-8") as f:
            ts = datetime.now(timezone.utc).isoformat()
            f.write(f"{ts} main: {message}\n")
    except Exception:
        pass


# ── Platform detection ──────────────────────────────────────────────────────
_SYSTEM = platform.system()
IS_MOBILE = _SYSTEM not in ("Windows", "Darwin", "Linux")
# Override: if running as Flet web/iOS app, detect via flet platform
# (flet sets sys.platform to 'ios' or 'android' when built natively)
if sys.platform in ("ios", "android"):
    IS_MOBILE = True

IS_WINDOWS = _SYSTEM == "Windows"


def _get_data_provider():
    """Return the appropriate data provider module."""
    if IS_MOBILE:
        import utils.api_client as client
        # Restore saved server URL
        return client
    else:
        import db.database as db_ops
        db_ops.init_db()
        return db_ops


# ── Navigation route constants ──────────────────────────────────────────────
ROUTE_HOME        = "home"
ROUTE_PROTOCOLS   = "protocols"
ROUTE_BOX         = "box"
ROUTE_SETTINGS    = "settings"
ROUTE_STEPPER     = "stepper"
ROUTE_HISTORY     = "history"
ROUTE_REPORT      = "report"
ROUTE_BOX_CHECKIN = "box_checkin"
ROUTE_PHOTO_REVIEW= "photo_review"
ROUTE_PROTO_EDIT  = "protocol_editor"
ROUTE_PROTO_IMPORT= "protocol_import"


# ── App ─────────────────────────────────────────────────────────────────────

def main(page: ft.Page) -> None:
    page.title = "ELN App"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = ft.Colors.GREY_50
    page.padding = 0

    # Custom theme: orange accent
    page.theme = ft.Theme(
        color_scheme_seed=ft.Colors.ORANGE_600,
        use_material3=True,
    )

    # ── Start server on Windows ─────────────────
    if IS_WINDOWS:
        try:
            from server.startup import start_server
            start_server()
        except Exception as e:
            print(f"Server start failed: {e}")

    # ── Data provider ───────────────────────────
    data_provider = _get_data_provider()

    # ── Restore server URL on mobile ────────────
    if IS_MOBILE:
        saved_url = page.client_storage.get("server_url")
        if saved_url:
            import utils.api_client as client
            client.set_base_url(saved_url)

    # ── Timer manager ───────────────────────────
    from timer_manager import get_timer_manager
    from notifications import notify_timer_finished
    import db.database as db_local

    tm = get_timer_manager()

    def _on_timer_finished(state):
        """Called when any countdown hits zero — fire notification."""
        try:
            # Get step title and experiment name for notification
            step = db_local.get_step(state.step_id)
            exp  = db_local.get_experiment(state.experiment_id)
            step_title = step.title if step else "步骤"
            exp_name   = exp.name if exp else "实验"
            notify_timer_finished(step_title, exp_name)
        except Exception:
            notify_timer_finished("步骤", "实验")

    tm.subscribe_finished(_on_timer_finished)
    tm.restore_from_db()
    tm.start()

    # ── Navigation state ─────────────────────────
    _current_tab: list[str] = [ROUTE_HOME]
    _nav_params: list[dict] = [{}]
    _refresh_nav_language: list = [lambda: None]

    content_area = ft.Container(expand=True)

    # ── Route → view builder ─────────────────────
    def _navigate(route: str, params: dict = None) -> None:
        if params is None:
            params = {}
        _debug_log(f"navigate route={route} params={params}")
        _current_tab[0] = route
        _nav_params[0] = params
        _render_route(route, params)
        _update_nav_selection(route)

    def _render_route(route: str, params: dict) -> None:
        exp_id = params.get("experiment_id")

        if route == ROUTE_HOME:
            from views.home_view import build_home_view
            view = build_home_view(
                page=page,
                data_provider=data_provider,
                on_open_experiment=lambda eid: _navigate(ROUTE_STEPPER, {"experiment_id": eid}),
                on_open_history=lambda: _navigate(ROUTE_HISTORY),
                on_open_protocols=lambda: _navigate(ROUTE_PROTOCOLS),
                is_mobile=IS_MOBILE,
            )

        elif route == ROUTE_PROTOCOLS:
            from views.protocol_library_view import build_protocol_library_view
            view = build_protocol_library_view(
                page=page,
                data_provider=data_provider,
                on_new_experiment=_create_experiment_and_navigate,
                on_edit_protocol=lambda pid: _navigate(ROUTE_PROTO_EDIT, {"protocol_id": pid}),
                on_import_protocol=lambda: _navigate(ROUTE_PROTO_IMPORT),
                is_mobile=IS_MOBILE,
            )

        elif route == ROUTE_BOX:
            from views.box_manager_view import build_box_manager_view
            view = build_box_manager_view(
                page=page,
                data_provider=data_provider,
                is_mobile=IS_MOBILE,
            )

        elif route == ROUTE_SETTINGS:
            from views.settings_view import build_settings_view
            view = build_settings_view(
                page=page,
                is_mobile=IS_MOBILE,
                on_server_url_changed=lambda url: None,
                on_language_changed=lambda: _refresh_language_shell(),
            )

        elif route == ROUTE_STEPPER:
            from views.stepper_view import StepperView
            view = StepperView(
                experiment_id=exp_id,
                on_back=lambda: _navigate(ROUTE_HOME),
                on_complete=lambda eid: _navigate(ROUTE_HOME),
                is_mobile=IS_MOBILE,
                data_provider=data_provider,
                navigate_to=_navigate,
                focus_step_id=params.get("step_id"),
            )

        elif route == ROUTE_HISTORY:
            from views.history_view import build_history_view
            view = build_history_view(
                page=page,
                data_provider=data_provider,
                on_back=lambda: _navigate(ROUTE_HOME),
                on_open_report=lambda eid: _navigate(ROUTE_REPORT, {"experiment_id": eid}),
                on_reuse_protocol=_create_experiment_and_navigate,
                on_continue_experiment=_open_existing_experiment,
                is_mobile=IS_MOBILE,
            )

        elif route == ROUTE_REPORT:
            from views.report_view import build_report_view
            view = build_report_view(
                page=page,
                data_provider=data_provider,
                experiment_id=exp_id,
                on_back=lambda: _navigate(ROUTE_HOME),
                is_mobile=IS_MOBILE,
            )

        elif route == ROUTE_BOX_CHECKIN:
            from views.box_checkin_view import build_box_checkin_view
            view = build_box_checkin_view(
                page=page,
                data_provider=data_provider,
                experiment_id=exp_id,
                on_done=lambda: _navigate(ROUTE_REPORT, {"experiment_id": exp_id}),
                on_skip=lambda: _navigate(ROUTE_REPORT, {"experiment_id": exp_id}),
                is_mobile=IS_MOBILE,
            )

        elif route == ROUTE_PHOTO_REVIEW:
            from views.photo_review_view import build_photo_review_view
            view = build_photo_review_view(
                page=page,
                data_provider=data_provider,
                experiment_id=exp_id,
                on_done=lambda: _navigate(ROUTE_BOX_CHECKIN, {"experiment_id": exp_id}),
                is_mobile=IS_MOBILE,
            )

        elif route == ROUTE_PROTO_EDIT:
            from views.protocol_editor_view import build_protocol_editor_view
            view = build_protocol_editor_view(
                page=page,
                data_provider=data_provider,
                protocol_id=params.get("protocol_id"),
                initial_json=params.get("initial_json"),
                on_save=lambda pid: _navigate(ROUTE_PROTOCOLS),
                on_cancel=lambda: _navigate(ROUTE_PROTOCOLS),
                is_mobile=IS_MOBILE,
            )

        elif route == ROUTE_PROTO_IMPORT:
            from views.protocol_import_view import build_protocol_import_view
            view = build_protocol_import_view(
                page=page,
                data_provider=data_provider,
                on_save_to_library=lambda pid: _navigate(ROUTE_PROTOCOLS),
                on_start_experiment=_create_experiment_and_navigate,
                on_cancel=lambda: _navigate(ROUTE_PROTOCOLS),
                is_mobile=IS_MOBILE,
            )

        else:
            view = ft.Container(
                content=ft.Text(f"未知路由：{route}", color=ft.Colors.RED_400),
                padding=20,
            )

        content_area.content = view
        try:
            page.update()
            _debug_log(f"rendered route={route}")
        except Exception as ex:
            _debug_log(f"page.update failed route={route}: {type(ex).__name__}: {ex}")
            content_area.content = ft.Container(
                content=ft.Column([
                    ft.Text("页面加载失败", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700),
                    ft.Text(str(ex), color=ft.Colors.RED_600, selectable=True),
                    ft.ElevatedButton("返回首页", on_click=lambda _: _navigate(ROUTE_HOME)),
                ], spacing=12),
                padding=24,
            )
            try:
                page.update()
                _debug_log(f"rendered route_error={route}")
            except Exception as fallback_ex:
                _debug_log(f"route_error update failed route={route}: {type(fallback_ex).__name__}: {fallback_ex}")

    def _refresh_language_shell() -> None:
        """Rebuild shell labels when the interface language changes."""
        _refresh_nav_language[0]()
        _render_route(_current_tab[0], _nav_params[0])
        _update_nav_selection(_current_tab[0])

    def _create_experiment_and_navigate(exp_params: dict) -> None:
        """Create experiment from protocol dict and open the native runner."""
        try:
            result = data_provider.create_experiment(
                name=exp_params["name"],
                protocol=_parse_protocol(exp_params["protocol_json"]),
                protocol_id=exp_params.get("protocol_id"),
            )
            exp_id = result.id if hasattr(result, "id") else result["id"]
            page.launch_url(
                ft.Url(
                    f"{_native_runner_url()}/run?experiment_id={int(exp_id)}",
                    target=ft.UrlTarget.SELF,
                ),
                web_popup_window_name=ft.UrlTarget.SELF,
            )
        except Exception as e:
            _open_overlay(page, ft.SnackBar(                content=ft.Text(f"创建实验失败：{e}"),                bgcolor=ft.Colors.RED_400,            ))

    def _open_existing_experiment(exp_id: int) -> None:
        page.launch_url(
            ft.Url(
                f"{_native_runner_url()}/run?experiment_id={int(exp_id)}",
                target=ft.UrlTarget.SELF,
            ),
            web_popup_window_name=ft.UrlTarget.SELF,
        )

    def _parse_protocol(json_str: str):
        from db.models import ProtocolDefinition
        return ProtocolDefinition.from_json(json_str)

    def _native_runner_url() -> str:
        configured = "" if os.environ.get("ELN_DYNAMIC_PUBLIC_URL") == "1" else os.environ.get("ELN_API_PUBLIC_URL", "").rstrip("/")
        if configured:
            return configured
        try:
            from server.startup import get_local_ip
            return f"http://{get_local_ip()}:8000"
        except Exception:
            return "http://127.0.0.1:8000"

    # ── Navigation bar ───────────────────────────
    _TAB_ROUTES = [ROUTE_HOME, ROUTE_PROTOCOLS, ROUTE_BOX, ROUTE_SETTINGS]

    if IS_MOBILE:
        nav = _build_mobile_nav(_navigate, _TAB_ROUTES)
        _nav_ref: list = [nav]

        def _update_nav_selection(route: str):
            idx = _TAB_ROUTES.index(route) if route in _TAB_ROUTES else -1
            if idx >= 0:
                _nav_ref[0].selected_index = idx
                try:
                    page.update()
                except Exception:
                    pass

        page.navigation_bar = nav

        def _refresh_mobile_nav_language() -> None:
            old_idx = getattr(_nav_ref[0], "selected_index", 0)
            new_nav = _build_mobile_nav(_navigate, _TAB_ROUTES)
            new_nav.selected_index = old_idx
            _nav_ref[0] = new_nav
            page.navigation_bar = new_nav
            page.update()

        _refresh_nav_language[0] = _refresh_mobile_nav_language
        page.on_resize = lambda _: page.update()

    else:
        rail = _build_desktop_rail(_navigate, _TAB_ROUTES)
        _nav_ref: list = [rail]
        _layout_row_ref: list = [None]
        _DESKTOP_ROUTES = [
            ROUTE_HOME, ROUTE_PROTOCOLS, ROUTE_BOX, ROUTE_HISTORY, ROUTE_SETTINGS
        ]

        def _update_nav_selection(route: str):
            idx = _DESKTOP_ROUTES.index(route) if route in _DESKTOP_ROUTES else -1
            if idx >= 0:
                _nav_ref[0].selected_index = idx
                try:
                    page.update()
                except Exception:
                    pass

        def _refresh_desktop_nav_language() -> None:
            old_idx = getattr(_nav_ref[0], "selected_index", 0)
            new_rail = _build_desktop_rail(_navigate, _TAB_ROUTES)
            new_rail.selected_index = old_idx
            _nav_ref[0] = new_rail
            if _layout_row_ref[0] is not None:
                _layout_row_ref[0].controls[0] = new_rail
            page.update()

        _refresh_nav_language[0] = _refresh_desktop_nav_language

    # ── Page layout ──────────────────────────────
    if IS_MOBILE:
        page.add(content_area)
    else:
        layout_row = ft.Row([
            rail,
            ft.VerticalDivider(width=1, color=ft.Colors.GREY_200),
            content_area,
        ], expand=True, spacing=0)
        _layout_row_ref[0] = layout_row
        page.add(layout_row)

    def _on_route_change(_) -> None:
        route, params = _get_initial_route(page)
        _navigate(route, params)

    page.on_route_change = _on_route_change

    # ── Initial route ────────────────────────────
    initial_route, initial_params = _get_initial_route(page)
    _navigate(initial_route, initial_params)

    # ── Cleanup on close ─────────────────────────
    def _on_disconnect(_):
        tm.stop()
        if IS_WINDOWS and os.environ.get("ELN_WEB_MODE") != "1":
            try:
                from server.startup import stop_server
                stop_server()
            except Exception:
                pass

    page.on_disconnect = _on_disconnect


def _get_initial_route(page: ft.Page) -> tuple[str, dict]:
    path_route = _path_initial_route(page)
    if path_route:
        return path_route

    route = _query_value(page, "route") or ROUTE_HOME
    if route != ROUTE_STEPPER:
        return ROUTE_HOME, {}

    try:
        exp_id = int(_query_value(page, "experiment_id") or "0")
    except ValueError:
        exp_id = 0
    try:
        step_id = int(_query_value(page, "step_id") or "0")
    except ValueError:
        step_id = 0
    if exp_id <= 0:
        return ROUTE_HOME, {}
    params = {"experiment_id": exp_id}
    if step_id > 0:
        params["step_id"] = step_id
    return ROUTE_STEPPER, params


def _path_initial_route(page: ft.Page) -> tuple[str, dict] | None:
    for source in (getattr(page, "route", ""), getattr(page, "url", "")):
        try:
            parsed = urlparse(str(source))
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] == "stepper":
                exp_id = int(parts[1])
                params = {"experiment_id": exp_id}
                if len(parts) >= 3:
                    step_id = int(parts[2])
                    if step_id > 0:
                        params["step_id"] = step_id
                return ROUTE_STEPPER, params
        except Exception:
            pass
    return None


def _read_stored_return_target() -> tuple[str, dict] | None:
    path = os.path.join(os.path.expanduser("~"), "ELN_Data", "web_return.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        try:
            os.remove(path)
        except OSError:
            pass
        if time.time() - float(data.get("created_at", 0)) > 600:
            return None
        if data.get("route") != ROUTE_STEPPER:
            return None
        exp_id = int(data.get("experiment_id", 0))
        step_id = int(data.get("step_id", 0))
        if exp_id <= 0 or step_id <= 0:
            return None
        return ROUTE_STEPPER, {"experiment_id": exp_id, "step_id": step_id}
    except Exception:
        return None


def _query_value(page: ft.Page, key: str) -> str | None:
    query = getattr(page, "query", None)
    if hasattr(query, "get"):
        try:
            value = query.get(key)
            if value is not None:
                return str(value)
        except Exception:
            pass

    for source in (getattr(page, "url", ""), getattr(page, "route", ""), str(query or "")):
        try:
            parsed = urlparse(str(source))
            values = parse_qs(parsed.query or str(source).lstrip("?"))
            if key in values and values[key]:
                return values[key][0]
        except Exception:
            pass
    return None


# ── Mobile bottom navigation bar ────────────────────────────────────────────

def _build_mobile_nav(navigate_fn, tab_routes: list) -> ft.NavigationBar:
    def _on_change(e):
        idx = e.control.selected_index
        if 0 <= idx < len(tab_routes):
            navigate_fn(tab_routes[idx])

    return ft.NavigationBar(
        selected_index=0,
        on_change=_on_change,
        bgcolor=ft.Colors.WHITE,
        indicator_color=ft.Colors.ORANGE_100,
        destinations=[
            ft.NavigationBarDestination(
                icon=ft.Icons.HOME_OUTLINED,
                selected_icon=ft.Icons.HOME,
                label=tr("首页"),
            ),
            ft.NavigationBarDestination(
                icon=ft.Icons.DESCRIPTION_OUTLINED,
                selected_icon=ft.Icons.DESCRIPTION,
                label=tr("协议"),
            ),
            ft.NavigationBarDestination(
                icon=ft.Icons.INVENTORY_2_OUTLINED,
                selected_icon=ft.Icons.INVENTORY_2,
                label=tr("Box"),
            ),
            ft.NavigationBarDestination(
                icon=ft.Icons.SETTINGS_OUTLINED,
                selected_icon=ft.Icons.SETTINGS,
                label=tr("设置"),
            ),
        ],
    )


# ── Desktop left navigation rail ────────────────────────────────────────────

def _build_desktop_rail(navigate_fn, tab_routes: list) -> ft.NavigationRail:
    _DESKTOP_ROUTES = [
        ROUTE_HOME, ROUTE_PROTOCOLS, ROUTE_BOX, ROUTE_HISTORY, ROUTE_SETTINGS
    ]

    def _on_change(e):
        idx = e.control.selected_index
        if 0 <= idx < len(_DESKTOP_ROUTES):
            navigate_fn(_DESKTOP_ROUTES[idx])

    return ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        on_change=_on_change,
        bgcolor=ft.Colors.WHITE,
        indicator_color=ft.Colors.ORANGE_100,
        min_width=80,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icons.HOME_OUTLINED,
                selected_icon=ft.Icons.HOME,
                label=tr("首页"),
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.DESCRIPTION_OUTLINED,
                selected_icon=ft.Icons.DESCRIPTION,
                label=tr("协议库"),
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.INVENTORY_2_OUTLINED,
                selected_icon=ft.Icons.INVENTORY_2,
                label=tr("Box"),
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.HISTORY,
                selected_icon=ft.Icons.HISTORY,
                label=tr("历史"),
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.SETTINGS_OUTLINED,
                selected_icon=ft.Icons.SETTINGS,
                label=tr("设置"),
            ),
        ],
    )


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ft.app(main)
