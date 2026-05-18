from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_URLS = {
    "powerbi": "https://app.powerbi.com/",
    "tableau": "https://prod-ch-a.online.tableau.com/",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a Playwright authenticated storage state for published report tests."
    )
    parser.add_argument("--service", choices=sorted(DEFAULT_URLS), required=True)
    parser.add_argument("--url", default="", help="Optional report/site URL to open after login.")
    parser.add_argument("--output", required=True, help="Path to write the storage-state JSON file.")
    parser.add_argument("--profile-dir", default="", help="Optional Chromium user data directory to persist the full browser profile.")
    parser.add_argument("--timeout-ms", type=int, default=600000)
    args = parser.parse_args()

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    start_url = _validated_start_url(args.url, args.service)
    with sync_playwright() as playwright:
        profile_dir = Path(args.profile_dir).expanduser() if args.profile_dir.strip() else None
        if profile_dir is not None:
            profile_dir.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                viewport={"width": 1600, "height": 1200},
                locale="en-US",
            )
            browser = None
        else:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(viewport={"width": 1600, "height": 1200}, locale="en-US")
        try:
            page = context.new_page()
            print(f"Opening {args.service} login page. Complete sign-in in the browser window.")
            page.goto(start_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            _wait_until_authenticated(page, context, args.service, args.timeout_ms)
            context.storage_state(path=str(output_path), indexed_db=True)
        finally:
            context.close()
            if browser is not None:
                browser.close()

    _validate_storage_state(output_path)
    print(json.dumps({"status": "saved", "service": args.service, "storage_state_path": str(output_path)}, indent=2))
    return 0


def _wait_until_authenticated(page, context, service: str, timeout_ms: int) -> None:
    deadline = max(timeout_ms, 30000)
    step_ms = 1000
    elapsed = 0
    last_reason = ""

    while elapsed <= deadline:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except PlaywrightTimeoutError:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightTimeoutError:
            pass

        auth_reason = _detect_auth_required(page, service)
        if (
            not auth_reason
            and _has_service_session_state(context, service)
            and _service_page_ready(page, service)
        ):
            return
        last_reason = auth_reason or "waiting for authenticated service page"
        page.wait_for_timeout(step_ms)
        elapsed += step_ms

    raise TimeoutError(
        f"Timed out waiting for authenticated {service} session. Last detected state: {last_reason}"
    )


def _validated_start_url(raw_url: str, service: str) -> str:
    url = str(raw_url or "").strip()
    if not url:
        return DEFAULT_URLS[service]
    if url.startswith("<") and url.endswith(">"):
        raise ValueError(
            "Replace the placeholder --url value with the actual published report URL, "
            "or omit --url to open the service home page."
        )
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"--url must be a full http(s) URL, got: {url}")
    return url


def _detect_auth_required(page, service: str) -> str:
    current_url = str(getattr(page, "url", "") or "").lower()
    if any(
        marker in current_url
        for marker in [
            "login.microsoftonline.com",
            "login.live.com",
            "app.powerbi.com/singlesignon",
            "/signin",
            "/auth/signin",
        ]
    ):
        return "current page is a sign-in URL"

    try:
        body_text = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        body_text = ""
    if not body_text.strip():
        return "page is still loading"

    tableau_login = (
        "sign in to tableau cloud" in body_text
        or ("tableau" in body_text and "sign in" in body_text and "password" in body_text)
        or ("tableau" in body_text and "email" in body_text and "remember me" in body_text)
        or ("sign in with salesforce" in body_text and "tableau" in body_text)
    )
    powerbi_login = "enter your email" in body_text and "power bi" in body_text
    microsoft_login = "sign in" in body_text and "microsoft" in body_text and "email" in body_text
    if tableau_login or powerbi_login or microsoft_login:
        return "current page contains a sign-in form"

    if service == "tableau" and "tableau" in body_text:
        return ""
    if service == "powerbi" and ("power bi" in body_text or "workspace" in body_text or "report" in body_text):
        return ""
    return ""


def _service_page_ready(page, service: str) -> bool:
    current_url = str(getattr(page, "url", "") or "").lower()
    try:
        body_text = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        body_text = ""

    if service == "tableau":
        if "online.tableau.com" not in current_url:
            return False
        if any(marker in current_url for marker in ["/signin", "/auth/signin"]):
            return False
        return any(
            marker in current_url or marker in body_text
            for marker in ["workbooks", "views", "projects", "explore", "favorites", "recents"]
        )

    if service == "powerbi":
        if "powerbi.com" not in current_url:
            return False
        if "singlesignon" in current_url or "login" in current_url:
            return False
        return any(marker in body_text for marker in ["power bi", "workspace", "report", "view report"])

    return bool(body_text.strip())


def _has_service_session_state(context, service: str) -> bool:
    try:
        state = context.storage_state()
    except Exception:
        return False
    cookies = state.get("cookies") if isinstance(state, dict) else []
    origins = state.get("origins") if isinstance(state, dict) else []
    if not isinstance(cookies, list):
        cookies = []
    if not isinstance(origins, list):
        origins = []

    domain_tokens = {
        "powerbi": ["powerbi.com", "microsoftonline.com"],
        "tableau": ["tableau.com", "salesforce.com"],
    }.get(service, [service])
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain") or "").lower()
        if any(token in domain for token in domain_tokens):
            return True
    for origin in origins:
        if not isinstance(origin, dict):
            continue
        origin_value = str(origin.get("origin") or "").lower()
        if any(token in origin_value for token in domain_tokens):
            return True
    return False


def _validate_storage_state(path: Path) -> None:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    cookies = payload.get("cookies")
    origins = payload.get("origins")
    if not cookies and not origins:
        raise RuntimeError(f"Storage state was written but appears empty: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
