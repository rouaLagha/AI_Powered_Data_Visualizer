from __future__ import annotations

import argparse
import glob
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from pywinauto import Desktop
from pywinauto.keyboard import send_keys


ROOT_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "llm_config.json"
DEFAULT_TWBX_PATH = OUTPUT_DIR / "converted_report.twbx"
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_PUBLISH_WAIT_SECONDS = 240


@dataclass
class DesktopRpaConfig:
    twbx_path: Path
    tableau_exe: Path
    email: str
    password: str
    workbook_name: str
    timeout_seconds: int
    publish_wait_seconds: int
    launch_settle_seconds: int
    cdp_enabled: bool
    cdp_port_start: int
    cdp_port_end: int
    cdp_timeout_seconds: int
    dry_run: bool


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _first_non_empty(values: Iterable[str | None]) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _coerce_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except Exception:
        return default
    return parsed if parsed > 0 else default


def _coerce_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        return default
    return bool(value)


def _resolve_tableau_exe(explicit_value: str | None) -> Path:
    if explicit_value:
        candidate = Path(explicit_value).expanduser().resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"Tableau executable not found: {candidate}")
        return candidate

    env_candidate = os.getenv("TABLEAU_PUBLIC_EXE")
    if env_candidate:
        candidate = Path(env_candidate).expanduser().resolve()
        if candidate.exists():
            return candidate

    patterns = [
        r"C:\\Program Files\\Tableau\\Tableau Public*\\bin\\tabpublic.exe",
        r"C:\\Program Files\\Tableau\\Tableau Public*\\bin\\TabPublic.exe",
        r"C:\\Program Files\\Tableau\\Tableau Public*\\bin\\tableau.exe",
        r"C:\\Program Files\\Tableau\\Tableau Public*\\bin\\Tableau.exe",
        r"C:\\Program Files\\Tableau\\Tableau Public*\\Tableau Public.exe",
        r"C:\\Program Files (x86)\\Tableau\\Tableau Public*\\bin\\tabpublic.exe",
        r"C:\\Program Files (x86)\\Tableau\\Tableau Public*\\bin\\TabPublic.exe",
        r"C:\\Program Files (x86)\\Tableau\\Tableau Public*\\bin\\tableau.exe",
        r"C:\\Program Files (x86)\\Tableau\\Tableau Public*\\Tableau Public.exe",
        r"C:\\Program Files\\Tableau\\Tableau *\\bin\\tableau.exe",
        r"C:\\Program Files (x86)\\Tableau\\Tableau *\\bin\\tableau.exe",
    ]

    found: list[Path] = []
    for pattern in patterns:
        for match in glob.glob(pattern):
            path = Path(match).resolve()
            if path.exists() and path not in found:
                found.append(path)

    if not found:
        raise FileNotFoundError(
            "Could not find Tableau Desktop/Public executable. "
            "Set --tableau-exe or TABLEAU_PUBLIC_EXE."
        )

    public_candidates = [
        path
        for path in found
        if "tableau public" in str(path).lower()
        or path.name.lower() in {"tabpublic.exe", "tableau public.exe"}
    ]

    # Prefer Tableau Public binaries when available; otherwise use the newest candidate.
    preferred = public_candidates if public_candidates else found
    preferred.sort()
    return preferred[-1]


def load_config(args: argparse.Namespace) -> DesktopRpaConfig:
    cfg_path = Path(args.config).resolve() if args.config else DEFAULT_CONFIG_PATH
    payload = _read_json(cfg_path)
    tableau_cfg = payload.get("tableau_server") if isinstance(payload, dict) else {}
    if not isinstance(tableau_cfg, dict):
        tableau_cfg = {}

    twbx_raw = _first_non_empty([args.twbx, os.getenv("TABLEAU_TWBX_PATH")])
    twbx_path = Path(twbx_raw).resolve() if twbx_raw else DEFAULT_TWBX_PATH.resolve()
    if not twbx_path.exists():
        raise FileNotFoundError(f"TWBX not found: {twbx_path}")

    workbook_name = _first_non_empty(
        [
            args.workbook_name,
            os.getenv("TABLEAU_WORKBOOK_NAME"),
            tableau_cfg.get("workbook_name"),
            twbx_path.stem,
        ]
    )

    email = _first_non_empty(
        [
            args.email,
            os.getenv("TABLEAU_EMAIL"),
            os.getenv("TABLEAU_USERNAME"),
            tableau_cfg.get("username"),
        ]
    )
    password = _first_non_empty(
        [
            args.password,
            os.getenv("TABLEAU_PASSWORD"),
            tableau_cfg.get("password"),
        ]
    )

    tableau_exe = _resolve_tableau_exe(args.tableau_exe)

    cdp_enabled_cfg = _coerce_bool(tableau_cfg.get("desktop_rpa_cdp_enabled"), True)
    cdp_enabled = cdp_enabled_cfg and not bool(args.disable_cdp)
    cdp_port_start = _coerce_positive_int(
        tableau_cfg.get("desktop_rpa_cdp_port_start"),
        args.cdp_port_start,
    )
    cdp_port_end = _coerce_positive_int(
        tableau_cfg.get("desktop_rpa_cdp_port_end"),
        args.cdp_port_end,
    )
    if cdp_port_end < cdp_port_start:
        cdp_port_end = cdp_port_start
    cdp_timeout_seconds = _coerce_positive_int(
        tableau_cfg.get("desktop_rpa_cdp_timeout_seconds"),
        args.cdp_timeout_seconds,
    )

    return DesktopRpaConfig(
        twbx_path=twbx_path,
        tableau_exe=tableau_exe,
        email=email,
        password=password,
        workbook_name=workbook_name,
        timeout_seconds=args.timeout_seconds,
        publish_wait_seconds=args.publish_wait_seconds,
        launch_settle_seconds=args.launch_settle_seconds,
        cdp_enabled=cdp_enabled,
        cdp_port_start=cdp_port_start,
        cdp_port_end=cdp_port_end,
        cdp_timeout_seconds=cdp_timeout_seconds,
        dry_run=args.dry_run,
    )


def _escape_send_keys_text(text: str) -> str:
    special = set("+^%~(){}[]")
    escaped: list[str] = []
    for ch in text:
        if ch in special:
            escaped.append(f"{{{ch}}}")
        else:
            escaped.append(ch)
    return "".join(escaped)


def _set_webview2_remote_debugging_env(env: dict[str, str], port: int) -> None:
    existing = str(env.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS") or "").strip()
    if "--remote-debugging-port" in existing:
        return
    remote_arg = f"--remote-debugging-port={port}"
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"{existing} {remote_arg}".strip()


def _fetch_json(url: str, timeout_seconds: float = 1.2) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "rdl-to-twb-rpa"})
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(payload)
        if isinstance(parsed, (dict, list)):
            return parsed
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    except Exception:
        return None
    return None


def _is_port_open(port: int, timeout_seconds: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout_seconds):
            return True
    except Exception:
        return False


def _discover_cdp_endpoint(
    port_start: int,
    port_end: int,
    timeout_seconds: int,
) -> dict | None:
    endpoint, _probe = _discover_cdp_endpoint_with_probe(
        port_start=port_start,
        port_end=port_end,
        timeout_seconds=timeout_seconds,
    )
    return endpoint


def _summarize_cdp_version_payload(port: int, payload: dict[str, Any]) -> dict[str, Any]:
    ws_url = payload.get("webSocketDebuggerUrl")
    return {
        "port": port,
        "browser": str(payload.get("Browser") or ""),
        "protocol_version": str(payload.get("Protocol-Version") or payload.get("ProtocolVersion") or ""),
        "user_agent": str(payload.get("User-Agent") or payload.get("UserAgent") or ""),
        "has_websocket_url": isinstance(ws_url, str) and bool(ws_url.strip()),
    }


def _discover_cdp_endpoint_with_probe(
    port_start: int,
    port_end: int,
    timeout_seconds: int,
) -> tuple[dict | None, dict[str, Any]]:
    started_at = time.time()
    deadline = started_at + max(1, timeout_seconds)

    attempts = 0
    tested_ports: set[int] = set()
    open_ports: set[int] = set()
    version_payloads: dict[int, dict[str, Any]] = {}

    while time.time() < deadline:
        attempts += 1
        for port in range(port_start, port_end + 1):
            tested_ports.add(port)
            if not _is_port_open(port):
                continue

            open_ports.add(port)
            version_payload = _fetch_json(f"http://127.0.0.1:{port}/json/version")
            if not isinstance(version_payload, dict):
                continue

            version_payloads[port] = _summarize_cdp_version_payload(port, version_payload)
            websocket_url = version_payload.get("webSocketDebuggerUrl")
            if not isinstance(websocket_url, str) or not websocket_url.strip():
                continue

            endpoint = {
                "port": port,
                "websocket_url": websocket_url,
                "browser": str(version_payload.get("Browser") or ""),
            }
            probe = {
                "attempt_count": attempts,
                "elapsed_seconds": round(time.time() - started_at, 2),
                "ports_tested": sorted(tested_ports),
                "open_ports": sorted(open_ports),
                "version_candidates": [version_payloads[p] for p in sorted(version_payloads)],
                "endpoint_found": True,
                "endpoint_port": port,
            }
            return endpoint, probe

        time.sleep(0.4)

    probe = {
        "attempt_count": attempts,
        "elapsed_seconds": round(time.time() - started_at, 2),
        "ports_tested": sorted(tested_ports),
        "open_ports": sorted(open_ports),
        "version_candidates": [version_payloads[p] for p in sorted(version_payloads)],
        "endpoint_found": False,
    }
    return None, probe


def _cdp_page_looks_like_login(page) -> bool:
    try:
        pwd_count = page.locator(
            "input[type='password'], input[name='password'], input[autocomplete='current-password']"
        ).count()
        if pwd_count > 0:
            return True
    except Exception:
        pass

    try:
        body_text = (page.locator("body").inner_text(timeout=800) or "").strip().lower()
        return bool(re.search(r"sign in|login|connexion|se connecter", body_text))
    except Exception:
        return False


def _cdp_try_fill_first(page, selectors: list[str], value: str) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() <= 0:
                continue
            if not locator.is_visible(timeout=1200):
                continue
            locator.click(timeout=1800)
            try:
                locator.fill(value, timeout=1800)
            except Exception:
                locator.press("Control+A", timeout=1200)
                locator.type(value, delay=20)
            return True
        except Exception:
            continue
    return False


def _cdp_try_click_first(page, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() <= 0:
                continue
            if not locator.is_visible(timeout=1200):
                continue
            locator.click(timeout=1800)
            return True
        except Exception:
            continue
    return False


def _collect_cdp_pages(browser) -> list:
    pages: list = []
    for context in browser.contexts:
        try:
            pages.extend(context.pages)
        except Exception:
            continue
    return pages


def _attempt_login_via_cdp(
    email: str,
    password: str,
    port_start: int,
    port_end: int,
    timeout_seconds: int,
) -> dict:
    if not email or not password:
        return {"status": "skipped", "reason": "missing_credentials"}

    endpoint, probe = _discover_cdp_endpoint_with_probe(
        port_start=port_start,
        port_end=port_end,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(endpoint, dict):
        return {
            "status": "unavailable",
            "reason": "cdp_endpoint_not_found",
            "cdp_probe": probe,
        }

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {
            "status": "unavailable",
            "reason": "playwright_not_installed_for_cdp",
            "port": endpoint.get("port"),
            "cdp_probe": probe,
        }

    pw = None
    browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{int(endpoint['port'])}")

        email_selectors = [
            "input[name='email']",
            "input[type='email']",
            "input[autocomplete='username']",
            "input[name='username']",
        ]
        password_selectors = [
            "input[name='password']",
            "input[type='password']",
            "input[autocomplete='current-password']",
        ]
        submit_selectors = [
            "button[type='submit']",
            "button:has-text('Sign in')",
            "button:has-text('Sign In')",
            "button:has-text('Se connecter')",
            "button:has-text('Connexion')",
            "input[type='submit']",
        ]

        deadline = time.time() + max(3, timeout_seconds)
        while time.time() < deadline:
            pages = _collect_cdp_pages(browser)
            for page in pages:
                try:
                    url = str(page.url or "")
                except Exception:
                    url = ""

                looks_login = _cdp_page_looks_like_login(page)
                if not looks_login and "tableau" not in url.lower():
                    continue

                email_filled = _cdp_try_fill_first(page, email_selectors, email)
                password_filled = _cdp_try_fill_first(page, password_selectors, password)
                if email_filled and password_filled:
                    submit_clicked = _cdp_try_click_first(page, submit_selectors)
                    if not submit_clicked:
                        try:
                            page.keyboard.press("Enter")
                            submit_clicked = True
                        except Exception:
                            submit_clicked = False
                    return {
                        "status": "typed_credentials",
                        "mode": "cdp",
                        "port": endpoint.get("port"),
                        "url": url,
                        "submit_clicked": submit_clicked,
                        "cdp_probe": probe,
                    }

                if any(token in url.lower() for token in ["/app/profile", "/newworkbook", "/viz/", "/views/"]):
                    return {
                        "status": "already_authenticated",
                        "mode": "cdp",
                        "port": endpoint.get("port"),
                        "url": url,
                        "cdp_probe": probe,
                    }

            time.sleep(0.5)

        return {
            "status": "not_detected",
            "reason": "cdp_login_form_not_found",
            "port": endpoint.get("port"),
            "cdp_probe": probe,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "reason": f"cdp_login_failed: {type(exc).__name__}",
            "port": endpoint.get("port"),
            "cdp_probe": probe,
        }
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass


def _complete_publish_dialog_via_cdp(
    workbook_name: str,
    port_start: int,
    port_end: int,
    timeout_seconds: int,
) -> dict:
    endpoint, probe = _discover_cdp_endpoint_with_probe(
        port_start=port_start,
        port_end=port_end,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(endpoint, dict):
        return {
            "status": "unavailable",
            "reason": "cdp_endpoint_not_found",
            "cdp_probe": probe,
        }

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {
            "status": "unavailable",
            "reason": "playwright_not_installed_for_cdp",
            "port": endpoint.get("port"),
            "cdp_probe": probe,
        }

    pw = None
    browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{int(endpoint['port'])}")

        workbook_name_selectors = [
            "input[name='workbookName']",
            "input[placeholder*='name' i]",
            "input[aria-label*='name' i]",
            "[data-testid='workbook-name-input'] input",
        ]
        submit_selectors = [
            "button:has-text('Publish')",
            "button:has-text('Save')",
            "button:has-text('Publier')",
            "button:has-text('Enregistrer')",
            "[data-testid='publish-button']",
        ]

        deadline = time.time() + max(3, timeout_seconds)
        while time.time() < deadline:
            pages = _collect_cdp_pages(browser)
            for page in pages:
                try:
                    url = str(page.url or "")
                except Exception:
                    url = ""

                if _cdp_page_looks_like_login(page):
                    continue

                if "tableau" not in url.lower():
                    continue

                if workbook_name:
                    _cdp_try_fill_first(page, workbook_name_selectors, workbook_name)

                if _cdp_try_click_first(page, submit_selectors):
                    return {
                        "status": "submitted",
                        "window_title": f"cdp:{url or 'unknown'}",
                        "mode": "cdp",
                        "port": endpoint.get("port"),
                        "cdp_probe": probe,
                    }

                try:
                    body_text = (page.locator("body").inner_text(timeout=800) or "").lower()
                except Exception:
                    body_text = ""

                if re.search(r"publish|save|enregistrer|publier", body_text):
                    try:
                        page.keyboard.press("Enter")
                        return {
                            "status": "submitted_keyboard_enter",
                            "window_title": f"cdp:{url or 'unknown'}",
                            "mode": "cdp",
                            "port": endpoint.get("port"),
                            "cdp_probe": probe,
                        }
                    except Exception:
                        pass

            time.sleep(0.5)

        return {
            "status": "not_detected",
            "reason": "cdp_publish_dialog_not_found",
            "port": endpoint.get("port"),
            "cdp_probe": probe,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "reason": f"cdp_publish_failed: {type(exc).__name__}",
            "port": endpoint.get("port"),
            "cdp_probe": probe,
        }
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass


def _wait_for_outcome_via_cdp(
    timeout_seconds: int,
    port_start: int,
    port_end: int,
) -> dict:
    endpoint, probe = _discover_cdp_endpoint_with_probe(
        port_start=port_start,
        port_end=port_end,
        timeout_seconds=min(timeout_seconds, 20),
    )
    if not isinstance(endpoint, dict):
        return {
            "status": "unavailable",
            "reason": "cdp_endpoint_not_found",
            "cdp_probe": probe,
        }

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {
            "status": "unavailable",
            "reason": "playwright_not_installed_for_cdp",
            "port": endpoint.get("port"),
            "cdp_probe": probe,
        }

    success_patterns = [
        re.compile(r"published|publication", re.IGNORECASE),
        re.compile(r"success|succes", re.IGNORECASE),
        re.compile(r"view workbook|view on tableau public", re.IGNORECASE),
    ]
    failure_patterns = [
        re.compile(r"error|failed|failure", re.IGNORECASE),
        re.compile(r"cannot|unable|impossible|echec", re.IGNORECASE),
    ]

    pw = None
    browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{int(endpoint['port'])}")

        deadline = time.time() + max(3, timeout_seconds)
        while time.time() < deadline:
            pages = _collect_cdp_pages(browser)
            for page in pages:
                try:
                    url = str(page.url or "")
                except Exception:
                    url = ""

                lower_url = url.lower()
                if any(token in lower_url for token in ["/viz/", "/views/"]):
                    return {
                        "status": "published",
                        "evidence": f"cdp_url:{url}",
                        "mode": "cdp",
                        "port": endpoint.get("port"),
                        "cdp_probe": probe,
                    }

                if _cdp_page_looks_like_login(page):
                    return {
                        "status": "failed",
                        "reason": "login_required",
                        "evidence": f"cdp_url:{url}",
                        "mode": "cdp",
                        "port": endpoint.get("port"),
                        "cdp_probe": probe,
                    }

                try:
                    body_text = page.locator("body").inner_text(timeout=800) or ""
                except Exception:
                    body_text = ""

                if any(rx.search(body_text) for rx in success_patterns):
                    return {
                        "status": "published",
                        "evidence": body_text[:500],
                        "mode": "cdp",
                        "port": endpoint.get("port"),
                        "cdp_probe": probe,
                    }
                if any(rx.search(body_text) for rx in failure_patterns):
                    return {
                        "status": "failed",
                        "evidence": body_text[:500],
                        "mode": "cdp",
                        "port": endpoint.get("port"),
                        "cdp_probe": probe,
                    }

            time.sleep(0.6)

        return {
            "status": "unknown",
            "reason": "cdp_publish_outcome_not_detected",
            "evidence": "No explicit publish success/failure detected on CDP pages.",
            "port": endpoint.get("port"),
            "cdp_probe": probe,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "reason": f"cdp_wait_outcome_failed: {type(exc).__name__}",
            "port": endpoint.get("port"),
            "cdp_probe": probe,
        }
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass


def _wait_for_tableau_window(pid: int, timeout_seconds: int):
    desktop = Desktop(backend="uia")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for win in desktop.windows():
            try:
                if win.process_id() != pid:
                    continue
                title = (win.window_text() or "").strip()
                if "tableau" in title.lower() and win.is_visible():
                    try:
                        win.set_focus()
                    except Exception:
                        pass
                    return win
            except Exception:
                continue
        time.sleep(0.5)
    raise TimeoutError("Could not find Tableau Public main window in time.")


def _find_descendant_by_text(window, text_patterns: list[str], control_types: list[str]):
    regexes = [re.compile(p, re.IGNORECASE) for p in text_patterns]
    for control_type in control_types:
        try:
            descendants = window.descendants(control_type=control_type)
        except Exception:
            continue
        for ctrl in descendants:
            try:
                text = (ctrl.window_text() or "").strip()
                if not text:
                    continue
                if any(rx.search(text) for rx in regexes) and ctrl.is_visible():
                    return ctrl
            except Exception:
                continue
    return None


def _iter_visible_windows(desktop: Desktop) -> list:
    visible: list = []
    for win in desktop.windows():
        try:
            if win.is_visible():
                visible.append(win)
        except Exception:
            continue
    return visible


def _safe_window_pid(window) -> int | None:
    try:
        return int(window.process_id())
    except Exception:
        return None


def _is_candidate_rpa_window(window) -> bool:
    try:
        title = (window.window_text() or "").strip().lower()
    except Exception:
        title = ""

    try:
        class_name = (window.class_name() or "").strip().lower()
    except Exception:
        class_name = ""

    if not title and not class_name:
        return False

    excluded_tokens = [
        "visual studio code",
        "taskbar",
        "powershell",
        "terminal",
        "command prompt",
        "explorer",
        "copilot",
    ]
    if any(token in title or token in class_name for token in excluded_tokens):
        return False

    include_tokens = [
        "tableau",
        "tabpublic",
        "sign in",
        "login",
        "connexion",
        "se connecter",
        "publish",
        "enregistrer",
        "chrome",
        "edge",
        "firefox",
        "webview",
    ]
    return any(token in title or token in class_name for token in include_tokens)


def _iter_candidate_windows(desktop: Desktop, preferred_pid: int | None = None) -> list:
    candidates = [win for win in _iter_visible_windows(desktop) if _is_candidate_rpa_window(win)]
    if preferred_pid is not None:
        candidates.sort(key=lambda win: 0 if _safe_window_pid(win) == preferred_pid else 1)
    return candidates


def _window_text_blob(window, max_controls: int = 150) -> str:
    parts: list[str] = []
    try:
        title = (window.window_text() or "").strip()
        if title:
            parts.append(title)
    except Exception:
        pass

    control_types = ["Text", "Edit", "Button", "Hyperlink", "Document", "Pane"]
    scanned = 0
    for control_type in control_types:
        if scanned >= max_controls:
            break
        try:
            descendants = window.descendants(control_type=control_type)
        except Exception:
            continue
        for ctrl in descendants:
            if scanned >= max_controls:
                break
            scanned += 1
            try:
                text = (ctrl.window_text() or "").strip()
            except Exception:
                continue
            if text:
                parts.append(text)

    return " ".join(parts)


def _window_matches_patterns(window, patterns: list[re.Pattern[str]]) -> bool:
    blob = _window_text_blob(window)
    if not blob:
        return False
    return any(rx.search(blob) for rx in patterns)


def _window_looks_like_login(window) -> bool:
    login_keyword = re.compile(r"\bsign in\b|se connecter|connexion|\blogin\b", re.IGNORECASE)
    user_keyword = re.compile(r"\bemail\b|username|identifiant", re.IGNORECASE)
    password_keyword = re.compile(r"password|mot de passe", re.IGNORECASE)

    blob = _window_text_blob(window)
    has_login = bool(login_keyword.search(blob))
    has_user = bool(user_keyword.search(blob))
    has_password = bool(password_keyword.search(blob))

    if has_login and (has_user or has_password):
        return True

    try:
        edits = window.descendants(control_type="Edit")
    except Exception:
        edits = []

    if len(edits) >= 2:
        aids = " ".join(
            str(getattr(edit.element_info, "automation_id", "") or "").lower()
            for edit in edits
        )
        if "email" in aids and "password" in aids:
            return True

    return False


def _snapshot_visible_windows(limit: int = 12, preferred_pid: int | None = None) -> list[str]:
    desktop = Desktop(backend="uia")
    items: list[str] = []
    windows = _iter_candidate_windows(desktop, preferred_pid=preferred_pid)
    if not windows:
        windows = _iter_visible_windows(desktop)

    for win in windows:
        if len(items) >= limit:
            break
        try:
            title = (win.window_text() or "").strip()
            class_name = (win.class_name() or "").strip()
            pid = win.process_id()
            items.append(f"pid={pid} class={class_name} title={title}")
        except Exception:
            continue
    return items


def _dismiss_interfering_popups(preferred_pid: int | None = None) -> list[str]:
    desktop = Desktop(backend="uia")
    dismissed: list[str] = []
    popup_patterns = [
        re.compile(r"gestionnaire de mots de passe", re.IGNORECASE),
        re.compile(r"password manager", re.IGNORECASE),
        re.compile(r"save password", re.IGNORECASE),
        re.compile(r"enregistrer.*mot de passe", re.IGNORECASE),
    ]

    windows = _iter_candidate_windows(desktop, preferred_pid=preferred_pid)
    if not windows:
        windows = _iter_visible_windows(desktop)

    for win in windows:
        try:
            title = (win.window_text() or "").strip()
            if not title:
                continue
            if not any(rx.search(title) for rx in popup_patterns):
                continue

            try:
                win.set_focus()
                send_keys("{ESC}")
            except Exception:
                try:
                    win.close()
                except Exception:
                    continue

            dismissed.append(title)
        except Exception:
            continue

    return dismissed


def _close_stale_tableau_windows(current_pid: int) -> list[str]:
    desktop = Desktop(backend="uia")
    closed: list[str] = []
    for win in _iter_visible_windows(desktop):
        try:
            pid = _safe_window_pid(win)
            if pid is None or pid == current_pid:
                continue

            title = (win.window_text() or "").strip()
            class_name = (win.class_name() or "").strip()
            low_title = title.lower()
            low_class = class_name.lower()

            # Restrict to likely Tableau main windows only.
            # Avoid matching unrelated apps whose title may include "tableau" as plain text.
            looks_like_tableau_main = (
                "tmainwindow" in low_class
                or low_title.startswith("tableau")
                or low_title.startswith("tableau public")
            )
            if not looks_like_tableau_main:
                continue

            try:
                win.close()
            except Exception:
                try:
                    win.set_focus()
                    send_keys("%{F4}")
                except Exception:
                    continue

            closed.append(f"pid={pid} title={title}")
        except Exception:
            continue

    return closed


def _trigger_publish_from_menu(main_window) -> str:
    file_patterns = [r"^file$", r"^fichier$"]
    publish_patterns = [
        r"save to tableau public",
        r"save to tableau public as",
        r"publish as",
        r"enregistrer.*tableau public",
        r"publier",
    ]

    file_ctrl = _find_descendant_by_text(main_window, file_patterns, ["MenuItem", "Button"])
    if file_ctrl is None:
        raise RuntimeError("File menu not found.")

    file_ctrl.click_input()
    time.sleep(1.0)

    desktop = Desktop(backend="uia")
    deadline = time.time() + 8
    while time.time() < deadline:
        for win in desktop.windows():
            publish_ctrl = _find_descendant_by_text(win, publish_patterns, ["MenuItem", "Button", "ListItem"])
            if publish_ctrl is not None:
                publish_ctrl.click_input()
                return "menu_click"
        time.sleep(0.2)

    raise RuntimeError("Publish action not found in File menu.")


def _trigger_publish_with_shortcuts(main_window) -> str:
    try:
        main_window.set_focus()
    except Exception:
        pass

    # Multiple fallbacks because shortcuts can differ by Tableau build/language.
    send_keys("^+s")
    time.sleep(1.5)
    send_keys("^s")
    time.sleep(1.5)
    send_keys("%f")
    time.sleep(0.8)
    return "shortcut_fallback"


def _find_login_window(timeout_seconds: int, preferred_pid: int | None = None):
    desktop = Desktop(backend="uia")
    title_patterns = [
        re.compile(r"sign in", re.IGNORECASE),
        re.compile(r"connexion", re.IGNORECASE),
        re.compile(r"login", re.IGNORECASE),
        re.compile(r"se connecter", re.IGNORECASE),
    ]
    login_keyword = re.compile(r"sign in|se connecter|connexion|login", re.IGNORECASE)
    user_keyword = re.compile(r"email|username|identifiant", re.IGNORECASE)
    password_keyword = re.compile(r"password|mot de passe", re.IGNORECASE)
    tableau_keyword = re.compile(r"tableau|public\.tableau\.com", re.IGNORECASE)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for win in _iter_candidate_windows(desktop, preferred_pid=preferred_pid):
            try:
                title = (win.window_text() or "").strip()
                if not title:
                    title = ""
                low = title.lower()

                try:
                    edit_count = len(win.descendants(control_type="Edit"))
                except Exception:
                    edit_count = 0

                if edit_count <= 0:
                    continue

                title_match = any(rx.search(title) for rx in title_patterns) if title else False
                blob = _window_text_blob(win)
                has_login_keyword = bool(login_keyword.search(blob))
                has_user = bool(user_keyword.search(blob))
                has_password = bool(password_keyword.search(blob))
                has_tableau = bool(tableau_keyword.search(blob)) or bool(tableau_keyword.search(title))

                browser_like = ("chrome" in low or "edge" in low or "firefox" in low or "webview" in low)
                browser_login_like = browser_like and ("tableau" in low) and (has_login_keyword or (has_user and has_password))

                # Prefer strong signals to avoid matching unrelated windows with incidental text.
                if (has_user and has_password) or browser_login_like:
                    return win

                if title_match and edit_count >= 1:
                    return win
            except Exception:
                continue
        time.sleep(0.4)
    return None


def _try_fill_login_form(login_window, email: str, password: str) -> bool:
    try:
        edits = login_window.descendants(control_type="Edit")
    except Exception:
        edits = []

    if not edits:
        return False

    user_edit = None
    password_edit = None
    for edit in edits:
        try:
            name = (edit.element_info.name or "").lower()
            aid = (edit.element_info.automation_id or "").lower()
            if password_edit is None and ("password" in name or "mot de passe" in name or "password" in aid):
                password_edit = edit
            if user_edit is None and (
                "email" in name
                or "username" in name
                or "identifiant" in name
                or "email" in aid
                or "user" in aid
            ):
                user_edit = edit
        except Exception:
            continue

    # Common fallback when fields are unlabeled.
    # Keep it conservative: when too many Edit controls are present, first/last is often wrong.
    if user_edit is None and len(edits) >= 1 and len(edits) <= 3:
        user_edit = edits[0]
    if password_edit is None and len(edits) >= 2 and len(edits) <= 3:
        password_edit = edits[-1]

    if user_edit is None or password_edit is None:
        return False

    try:
        user_edit.set_focus()
        try:
            user_edit.set_edit_text(email)
        except Exception:
            pass
        # Force keyboard typing because some WebView-hosted edits ignore set_edit_text.
        send_keys("^a")
        send_keys(_escape_send_keys_text(email), with_spaces=True)
        password_edit.set_focus()
        try:
            password_edit.set_edit_text(password)
        except Exception:
            pass
        send_keys("^a")
        send_keys(_escape_send_keys_text(password), with_spaces=True)
    except Exception:
        return False

    submit_controls = _collect_login_submit_controls(login_window)
    for submit_ctrl in submit_controls:
        try:
            submit_ctrl.click_input()
        except Exception:
            continue
        if _wait_for_login_submission_effect(login_window):
            return True

    submit_btn = _find_descendant_by_text(
        login_window,
        [r"^sign in$", r"^se connecter$", r"^connexion$", r"^login$", r"^continue$"],
        ["Button", "Hyperlink"],
    )
    if submit_btn is not None:
        try:
            submit_btn.click_input()
            if _wait_for_login_submission_effect(login_window):
                return True
        except Exception:
            pass

    try:
        login_window.set_focus()
        send_keys("{ENTER}")
        if _wait_for_login_submission_effect(login_window, timeout_seconds=5):
            return True
    except Exception:
        return False

    return False


def _collect_login_submit_controls(login_window) -> list[Any]:
    controls: list[tuple[int, int, Any]] = []
    primary_texts = {"sign in", "se connecter", "connexion", "login"}
    disallowed_tokens = [
        "salesforce",
        "google",
        "apple",
        "microsoft",
        "okta",
        "sso",
        "single sign",
    ]
    action_pattern = re.compile(r"sign in|se connecter|connexion|login|continue|next", re.IGNORECASE)

    for control_type in ["Button", "Hyperlink"]:
        try:
            descendants = login_window.descendants(control_type=control_type)
        except Exception:
            continue

        for ctrl in descendants:
            try:
                if not ctrl.is_visible():
                    continue
                raw_text = (ctrl.window_text() or "").strip()
                if not raw_text:
                    continue

                normalized = re.sub(r"\s+", " ", raw_text).strip().lower()
                aid = str(getattr(ctrl.element_info, "automation_id", "") or "").strip().lower()
                if any(token in normalized or token in aid for token in disallowed_tokens):
                    continue
                if not action_pattern.search(normalized):
                    continue

                priority = 2
                if normalized in primary_texts:
                    priority = 0
                elif normalized.startswith("sign in") or normalized.startswith("se connecter"):
                    priority = 1

                controls.append((priority, len(normalized), ctrl))
            except Exception:
                continue

    controls.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in controls]


def _wait_for_login_submission_effect(login_window, timeout_seconds: int = 6) -> bool:
    deadline = time.time() + max(1, timeout_seconds)
    stable_non_login_since: float | None = None
    while time.time() < deadline:
        try:
            if not login_window.exists() or not login_window.is_visible():
                if stable_non_login_since is None:
                    stable_non_login_since = time.time()
                elif time.time() - stable_non_login_since >= 1.0:
                    return True
                time.sleep(0.2)
                continue
        except Exception:
            return True

        if not _window_looks_like_login(login_window):
            if stable_non_login_since is None:
                stable_non_login_since = time.time()
            elif time.time() - stable_non_login_since >= 1.0:
                return True
        else:
            stable_non_login_since = None

        time.sleep(0.35)

    return False


def _attempt_login(email: str, password: str, timeout_seconds: int, preferred_pid: int | None = None) -> dict:
    if not email or not password:
        return {"status": "skipped", "reason": "missing_credentials"}

    login_window = _find_login_window(timeout_seconds=timeout_seconds, preferred_pid=preferred_pid)
    if login_window is None:
        return {"status": "not_detected", "reason": "login_window_not_found"}

    try:
        login_window.set_focus()
    except Exception:
        pass

    if _try_fill_login_form(login_window, email=email, password=password):
        return {"status": "typed_credentials", "window_title": login_window.window_text()}

    window_title = str(login_window.window_text() or "")
    low_title = window_title.lower()
    if "chrome" in low_title or "edge" in low_title or "firefox" in low_title:
        return {
            "status": "failed",
            "reason": "browser_login_form_controls_not_found",
            "window_title": window_title,
        }

    # Best effort generic typing sequence for embedded/brokered login dialogs.
    try:
        send_keys(_escape_send_keys_text(email), with_spaces=True)
        send_keys("{TAB}")
        send_keys(_escape_send_keys_text(password), with_spaces=True)
        send_keys("{ENTER}")
    except Exception as exc:
        return {
            "status": "failed",
            "reason": f"login_typing_failed: {type(exc).__name__}",
        }

    return {
        "status": "typed_credentials",
        "window_title": login_window.window_text(),
        "mode": "keyboard_fallback",
    }


def _complete_publish_dialog(
    workbook_name: str,
    timeout_seconds: int,
    preferred_pid: int | None = None,
) -> dict:
    desktop = Desktop(backend="uia")
    publish_title_patterns = [
        re.compile(r"publish", re.IGNORECASE),
        re.compile(r"save to tableau public", re.IGNORECASE),
        re.compile(r"enregistrer", re.IGNORECASE),
        re.compile(r"workbook", re.IGNORECASE),
    ]
    publish_action_patterns = [
        r"^save$",
        r"^publish$",
        r"^enregistrer$",
        r"^publier$",
        r"save to tableau public",
        r"publish to tableau public",
        r"publish workbook",
    ]

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for win in _iter_candidate_windows(desktop, preferred_pid=preferred_pid):
            try:
                if _window_looks_like_login(win):
                    continue

                title = (win.window_text() or "").strip()
                title_match = any(rx.search(title) for rx in publish_title_patterns) if title else False
                content_match = _window_matches_patterns(win, publish_title_patterns)

                save_ctrl = _find_descendant_by_text(
                    win,
                    publish_action_patterns,
                    ["Button", "MenuItem", "ListItem", "Hyperlink", "SplitButton", "Custom"],
                )

                if not title_match and not content_match and save_ctrl is None:
                    continue

                edits = win.descendants(control_type="Edit")
                if edits and workbook_name:
                    try:
                        edits[0].set_focus()
                        edits[0].set_edit_text(workbook_name)
                    except Exception:
                        pass

                if save_ctrl is not None:
                    save_ctrl.click_input()
                    return {"status": "submitted", "window_title": title}

                # Browser-hosted publish dialogs may not expose a clickable button in UIA.
                # Try a keyboard fallback: set workbook name then submit.
                low_title = title.lower()
                if "chrome" in low_title or "edge" in low_title:
                    try:
                        win.set_focus()
                        time.sleep(0.4)
                        if workbook_name:
                            send_keys("^a")
                            send_keys(_escape_send_keys_text(workbook_name), with_spaces=True)
                        send_keys("{TAB}{TAB}{ENTER}")
                        return {
                            "status": "submitted_keyboard_fallback",
                            "window_title": title,
                        }
                    except Exception:
                        pass

                # Non-browser fallback for Tableau Public desktop dialogs where UIA does not expose the default button.
                try:
                    win.set_focus()
                    if workbook_name and edits:
                        send_keys("^a")
                        send_keys(_escape_send_keys_text(workbook_name), with_spaces=True)
                    send_keys("{ENTER}")
                    return {
                        "status": "submitted_keyboard_enter",
                        "window_title": title,
                    }
                except Exception:
                    pass

                return {"status": "dialog_detected_but_no_submit", "window_title": title}
            except Exception:
                continue
        time.sleep(0.3)

    return {"status": "not_detected", "reason": "publish_dialog_not_found"}


def _wait_for_outcome(timeout_seconds: int, preferred_pid: int | None = None) -> dict:
    desktop = Desktop(backend="uia")
    success_patterns = [
        re.compile(r"published|publication", re.IGNORECASE),
        re.compile(r"success|succes", re.IGNORECASE),
        re.compile(r"open in browser|view workbook|view on tableau public", re.IGNORECASE),
    ]
    failure_patterns = [
        re.compile(r"error|failed|failure", re.IGNORECASE),
        re.compile(r"cannot|unable|impossible|echec", re.IGNORECASE),
    ]

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for win in _iter_candidate_windows(desktop, preferred_pid=preferred_pid):
            try:
                text = _window_text_blob(win)
                if not text:
                    continue

                if _window_looks_like_login(win):
                    return {
                        "status": "failed",
                        "reason": "login_required",
                        "evidence": text,
                    }

                if any(rx.search(text) for rx in success_patterns):
                    return {"status": "published", "evidence": text}
                if any(rx.search(text) for rx in failure_patterns):
                    return {"status": "failed", "evidence": text}
            except Exception:
                continue
        time.sleep(0.6)

    return {
        "status": "unknown",
        "reason": "publish_outcome_not_detected",
        "evidence": "No explicit publish success/failure dialog detected.",
    }


def run_rpa_publish(cfg: DesktopRpaConfig) -> dict:
    result: dict = {
        "status": "started",
        "tableau_exe": str(cfg.tableau_exe),
        "twbx_path": str(cfg.twbx_path),
        "workbook_name": cfg.workbook_name,
        "steps": [],
    }

    if cfg.dry_run:
        result["status"] = "dry_run"
        return result

    launch_env = os.environ.copy()
    if cfg.cdp_enabled:
        _set_webview2_remote_debugging_env(launch_env, cfg.cdp_port_start)
        result["steps"].append(
            {
                "step": "configure_cdp",
                "status": "ok",
                "port_start": cfg.cdp_port_start,
                "port_end": cfg.cdp_port_end,
            }
        )

    proc = subprocess.Popen(
        [str(cfg.tableau_exe), str(cfg.twbx_path)],
        cwd=str(cfg.twbx_path.parent),
        env=launch_env,
    )
    result["pid"] = proc.pid
    result["steps"].append({"step": "launch_tableau", "status": "ok", "pid": proc.pid})

    main_window = _wait_for_tableau_window(proc.pid, timeout_seconds=cfg.timeout_seconds)
    time.sleep(max(0, cfg.launch_settle_seconds))

    closed_stale = _close_stale_tableau_windows(current_pid=proc.pid)
    if closed_stale:
        result["steps"].append(
            {
                "step": "close_stale_tableau_windows",
                "status": "ok",
                "count": len(closed_stale),
                "windows": closed_stale,
            }
        )

    publish_mode = ""
    try:
        publish_mode = _trigger_publish_from_menu(main_window)
        result["steps"].append({"step": "trigger_publish", "status": "ok", "mode": publish_mode})
    except Exception as menu_exc:
        publish_mode = _trigger_publish_with_shortcuts(main_window)
        result["steps"].append(
            {
                "step": "trigger_publish",
                "status": "fallback",
                "mode": publish_mode,
                "detail": str(menu_exc),
            }
        )

    cdp_login_result: dict = {"status": "skipped", "reason": "cdp_disabled"}
    if cfg.cdp_enabled:
        cdp_login_result = _attempt_login_via_cdp(
            email=cfg.email,
            password=cfg.password,
            port_start=cfg.cdp_port_start,
            port_end=cfg.cdp_port_end,
            timeout_seconds=cfg.cdp_timeout_seconds,
        )
        result["steps"].append({"step": "login_cdp", **cdp_login_result})

    if cdp_login_result.get("status") in {"typed_credentials", "already_authenticated"}:
        login_result = {
            "status": str(cdp_login_result.get("status")),
            "window_title": str(cdp_login_result.get("url") or "cdp"),
            "mode": "cdp",
        }
    else:
        login_result = _attempt_login(
            cfg.email,
            cfg.password,
            timeout_seconds=cfg.timeout_seconds,
            preferred_pid=proc.pid,
        )
    result["steps"].append({"step": "login", **login_result})

    dismissed_popups = _dismiss_interfering_popups(preferred_pid=proc.pid)
    if dismissed_popups:
        result["steps"].append(
            {
                "step": "dismiss_interfering_popups",
                "status": "ok",
                "count": len(dismissed_popups),
                "titles": dismissed_popups,
            }
        )

    cdp_publish_result: dict = {"status": "skipped", "reason": "cdp_disabled"}
    if cfg.cdp_enabled:
        cdp_publish_result = _complete_publish_dialog_via_cdp(
            workbook_name=cfg.workbook_name,
            port_start=cfg.cdp_port_start,
            port_end=cfg.cdp_port_end,
            timeout_seconds=cfg.cdp_timeout_seconds,
        )
        result["steps"].append({"step": "publish_dialog_cdp", **cdp_publish_result})

    if cdp_publish_result.get("status") in {
        "submitted",
        "submitted_keyboard_enter",
        "submitted_keyboard_fallback",
    }:
        publish_dialog_result = cdp_publish_result
    else:
        publish_dialog_result = _complete_publish_dialog(
            cfg.workbook_name,
            timeout_seconds=cfg.timeout_seconds,
            preferred_pid=proc.pid,
        )
    result["steps"].append({"step": "publish_dialog", **publish_dialog_result})

    if publish_dialog_result.get("status") == "not_detected":
        retry_mode = _trigger_publish_with_shortcuts(main_window)
        result["steps"].append(
            {
                "step": "trigger_publish_retry",
                "status": "fallback",
                "mode": retry_mode,
            }
        )
        publish_dialog_result = _complete_publish_dialog(
            cfg.workbook_name,
            timeout_seconds=max(30, cfg.timeout_seconds // 2),
            preferred_pid=proc.pid,
        )
        result["steps"].append({"step": "publish_dialog_retry", **publish_dialog_result})

    cdp_outcome: dict = {"status": "skipped", "reason": "cdp_disabled"}
    if cfg.cdp_enabled:
        cdp_outcome = _wait_for_outcome_via_cdp(
            timeout_seconds=cfg.publish_wait_seconds,
            port_start=cfg.cdp_port_start,
            port_end=cfg.cdp_port_end,
        )
        result["steps"].append({"step": "wait_outcome_cdp", **cdp_outcome})

    if cdp_outcome.get("status") in {"published", "failed"}:
        outcome = cdp_outcome
    else:
        outcome = _wait_for_outcome(timeout_seconds=cfg.publish_wait_seconds, preferred_pid=proc.pid)
    result["steps"].append({"step": "wait_outcome", **outcome})

    if outcome.get("status") == "published":
        result["status"] = "published"
        result["reason"] = "publish_confirmed"
    elif outcome.get("status") == "failed":
        result["status"] = "failed"
        result["reason"] = str(outcome.get("reason") or "publish_failed")
    else:
        result["status"] = "needs_manual_check"
        if login_result.get("status") == "not_detected":
            result["reason"] = "login_window_not_detected"
        elif publish_dialog_result.get("status") == "not_detected":
            result["reason"] = "publish_dialog_not_detected"
        else:
            result["reason"] = str(outcome.get("reason") or "publish_outcome_not_detected")
        result["visible_windows"] = _snapshot_visible_windows(preferred_pid=proc.pid)

    result["message"] = (
        "RPA flow executed. If status is needs_manual_check, verify Tableau Public Desktop UI "
        "for MFA/CAPTCHA/login prompts or language-specific menu labels."
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RPA publish of TWBX using Tableau Public Desktop on Windows"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Config JSON path")
    parser.add_argument("--twbx", default=None, help="Path to TWBX package")
    parser.add_argument("--tableau-exe", default=None, help="Path to Tableau Public Desktop executable")
    parser.add_argument("--email", default=None, help="Tableau account email")
    parser.add_argument("--password", default=None, help="Tableau account password")
    parser.add_argument("--workbook-name", default=None, help="Workbook name used in publish dialog")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Timeout for each RPA stage")
    parser.add_argument(
        "--publish-wait-seconds",
        type=int,
        default=DEFAULT_PUBLISH_WAIT_SECONDS,
        help="Final wait for publish result detection",
    )
    parser.add_argument(
        "--launch-settle-seconds",
        type=int,
        default=4,
        help="Wait after Tableau main window appears before sending actions",
    )
    parser.add_argument(
        "--disable-cdp",
        action="store_true",
        help="Disable WebView2 CDP path and use only UIA/keyboard automation",
    )
    parser.add_argument(
        "--cdp-port-start",
        type=int,
        default=9222,
        help="First localhost port to probe for WebView2 CDP",
    )
    parser.add_argument(
        "--cdp-port-end",
        type=int,
        default=9232,
        help="Last localhost port to probe for WebView2 CDP",
    )
    parser.add_argument(
        "--cdp-timeout-seconds",
        type=int,
        default=25,
        help="Timeout for CDP endpoint discovery and CDP actions",
    )
    parser.add_argument("--dry-run", action="store_true", help="Resolve config and exit without launching Tableau")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args)
    result = run_rpa_publish(cfg)

    safe_cfg = asdict(cfg)
    safe_cfg["password"] = "***" if safe_cfg.get("password") else ""
    safe_cfg["twbx_path"] = str(cfg.twbx_path)
    safe_cfg["tableau_exe"] = str(cfg.tableau_exe)

    payload = {
        "config": safe_cfg,
        "result": result,
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
