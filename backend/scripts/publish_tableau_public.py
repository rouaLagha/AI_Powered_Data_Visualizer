from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout, async_playwright


ROOT_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "llm_config.json"
DEFAULT_TWBX_PATH = OUTPUT_DIR / "converted_report.twbx"
DEFAULT_SCREENSHOT_PATH = OUTPUT_DIR / "playwright_publish_debug.png"


@dataclass
class PublishConfig:
    email: str
    password: str
    twbx_path: Path
    workbook_name: str
    headless: bool
    timeout_ms: int
    slow_mo_ms: int
    screenshot_path: Path


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _truthy(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    text = value.strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _first_non_empty(values: Iterable[str | None]) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def load_publish_config(args: argparse.Namespace) -> PublishConfig:
    cfg_path = Path(args.config).resolve() if args.config else DEFAULT_CONFIG_PATH
    cfg_payload = _read_json(cfg_path)
    tableau_cfg = cfg_payload.get("tableau_server") if isinstance(cfg_payload, dict) else {}
    if not isinstance(tableau_cfg, dict):
        tableau_cfg = {}

    email = _first_non_empty(
        [
            args.email,
            os.getenv("TABLEAU_EMAIL"),
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

    twbx_raw = _first_non_empty([args.twbx, os.getenv("TABLEAU_TWBX_PATH")])
    twbx_path = Path(twbx_raw).resolve() if twbx_raw else DEFAULT_TWBX_PATH.resolve()

    workbook_name = _first_non_empty(
        [
            args.workbook_name,
            os.getenv("TABLEAU_WORKBOOK_NAME"),
            tableau_cfg.get("workbook_name"),
            twbx_path.stem,
        ]
    )

    headless = _truthy(os.getenv("TABLEAU_HEADLESS"), default=args.headless)
    timeout_ms = int(os.getenv("TABLEAU_TIMEOUT_MS", str(args.timeout_ms)))
    slow_mo_ms = int(os.getenv("TABLEAU_SLOW_MO_MS", str(args.slow_mo_ms)))

    screenshot_raw = _first_non_empty([args.screenshot, os.getenv("TABLEAU_DEBUG_SCREENSHOT")])
    screenshot_path = Path(screenshot_raw).resolve() if screenshot_raw else DEFAULT_SCREENSHOT_PATH.resolve()

    if not email:
        raise ValueError("Missing Tableau email. Set --email or TABLEAU_EMAIL.")
    if not password:
        raise ValueError("Missing Tableau password. Set --password or TABLEAU_PASSWORD.")
    if not twbx_path.exists():
        raise FileNotFoundError(f"TWBX not found: {twbx_path}")

    return PublishConfig(
        email=email,
        password=password,
        twbx_path=twbx_path,
        workbook_name=workbook_name,
        headless=headless,
        timeout_ms=timeout_ms,
        slow_mo_ms=slow_mo_ms,
        screenshot_path=screenshot_path,
    )


async def _wait_visible_and_fill(page: Page, selectors: list[str], value: str, timeout_ms: int) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            await locator.fill(value)
            return
        except PlaywrightTimeout:
            continue
    raise PlaywrightTimeout(f"None of the selectors became visible: {selectors}")


async def _click_first_visible(page: Page, selectors: list[str], timeout_ms: int) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            await locator.click()
            return
        except PlaywrightTimeout:
            continue
    raise PlaywrightTimeout(f"None of the click selectors became visible: {selectors}")


async def _open_my_profile(page: Page, timeout_ms: int) -> None:
    await _click_first_visible(
        page,
        selectors=[
            '[data-testid*="avatar" i]',
            'button[aria-label*="profile" i]',
            'button[aria-label*="account" i]',
        ],
        timeout_ms=timeout_ms,
    )
    await _click_first_visible(
        page,
        selectors=[
            '[data-testid="profileMenuItem"]',
            'li:has-text("My Profile")',
            'text=/My Profile/i',
        ],
        timeout_ms=timeout_ms,
    )


async def _open_web_authoring(page: Page, timeout_ms: int) -> None:
    try:
        await _click_first_visible(
            page,
            selectors=['button:has-text("Create a Viz")'],
            timeout_ms=timeout_ms,
        )
    except PlaywrightTimeout:
        # Fallback to top nav Create -> Web Authoring.
        await _click_first_visible(
            page,
            selectors=['[data-testid="CreateLink"]', 'button:has-text("Create")'],
            timeout_ms=timeout_ms,
        )
        await _click_first_visible(
            page,
            selectors=['li:has-text("Web Authoring")', 'text=/Web Authoring/i'],
            timeout_ms=timeout_ms,
        )

    try:
        await page.wait_for_url("**/newWorkbook/**", timeout=timeout_ms)
    except PlaywrightTimeout:
        if "newWorkbook" not in page.url:
            raise RuntimeError("Could not open Tableau Public web authoring page.")


async def _upload_from_computer(page: Page, file_path: Path, timeout_ms: int) -> None:
    upload_button = page.locator("text=/Upload from computer/i").first
    await upload_button.wait_for(state="visible", timeout=timeout_ms)

    try:
        async with page.expect_file_chooser(timeout=timeout_ms) as chooser_info:
            await upload_button.click()
        chooser = await chooser_info.value
        await chooser.set_files(str(file_path))
        return
    except PlaywrightTimeout:
        # Some builds expose a direct input instead of a file chooser event.
        file_input = page.locator('input[type="file"]').first
        await file_input.wait_for(state="attached", timeout=timeout_ms)
        await file_input.set_input_files(str(file_path))


async def _detect_upload_not_supported(page: Page) -> str | None:
    title_locator = page.locator("text=/Can't Upload Files/i").first
    detail_locator = page.locator("text=/file type is currently not supported on the web/i").first

    try:
        await title_locator.wait_for(state="visible", timeout=5000)
        title_text = (await title_locator.inner_text()).strip() or "Can't Upload Files"
        try:
            await detail_locator.wait_for(state="visible", timeout=2000)
            detail_text = (await detail_locator.inner_text()).strip()
            if detail_text:
                return f"{title_text}: {detail_text}"
        except PlaywrightTimeout:
            pass
        return title_text
    except PlaywrightTimeout:
        pass

    try:
        await detail_locator.wait_for(state="visible", timeout=3000)
        detail_text = (await detail_locator.inner_text()).strip()
        if detail_text:
            return detail_text
    except PlaywrightTimeout:
        pass

    return None


async def _try_set_workbook_name(page: Page, workbook_name: str) -> bool:
    selectors = [
        'input[name="workbookName"]',
        'input[placeholder*="name" i]',
        'input[aria-label*="name" i]',
        '[data-testid="workbook-name-input"] input',
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=6000)
            await locator.click()
            await locator.press("Control+A")
            await locator.fill(workbook_name)
            return True
        except PlaywrightTimeout:
            continue
    return False


async def _wait_until_published(page: Page) -> bool:
    url_patterns = ["**/viz/**", "**/views/**", "**/app/profile/**"]
    for pattern in url_patterns:
        try:
            await page.wait_for_url(pattern, timeout=120000)
            return True
        except PlaywrightTimeout:
            continue

    success_locators = [
        '[class*="success" i]',
        'text=/published|publi[e|é]/i',
    ]
    for selector in success_locators:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=15000)
            return True
        except PlaywrightTimeout:
            continue

    return False


async def publish_with_playwright(cfg: PublishConfig) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=cfg.headless, slow_mo=cfg.slow_mo_ms)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        try:
            print("-> Opening Tableau Public Discover page")
            await page.goto("https://public.tableau.com/app/discover", timeout=cfg.timeout_ms)

            print("-> Opening sign-in form")
            await _click_first_visible(
                page,
                selectors=[
                    'button:has-text("Sign In")',
                    'a:has-text("Sign In")',
                    'button:has-text("Log In")',
                    'a:has-text("Log In")',
                ],
                timeout_ms=cfg.timeout_ms,
            )

            try:
                await page.wait_for_url("**identity.idp.tableau.com/**", timeout=cfg.timeout_ms)
            except PlaywrightTimeout:
                # Sometimes the app displays the login form without URL host change.
                pass

            await _wait_visible_and_fill(
                page,
                selectors=[
                    'input[name="email"]',
                    'input[type="email"]',
                    'input[autocomplete="username"]',
                ],
                value=cfg.email,
                timeout_ms=cfg.timeout_ms,
            )
            await _wait_visible_and_fill(
                page,
                selectors=[
                    'input[name="password"]',
                    'input[type="password"]',
                    'input[autocomplete="current-password"]',
                ],
                value=cfg.password,
                timeout_ms=cfg.timeout_ms,
            )

            await _click_first_visible(
                page,
                selectors=[
                    'button[type="submit"]',
                    'button:has-text("Sign in")',
                    'button:has-text("Se connecter")',
                ],
                timeout_ms=cfg.timeout_ms,
            )

            try:
                await page.wait_for_url("**public.tableau.com/app/**", timeout=cfg.timeout_ms)
                print("-> Logged in")
            except PlaywrightTimeout as exc:
                raise RuntimeError("Login did not complete. Check credentials or captcha challenge.") from exc

            print("-> Opening My Profile")
            await _open_my_profile(page, timeout_ms=cfg.timeout_ms)

            print("-> Opening web authoring")
            await _open_web_authoring(page, timeout_ms=cfg.timeout_ms)

            print(f"-> Uploading file from computer: {cfg.twbx_path.name}")
            await _upload_from_computer(page, file_path=cfg.twbx_path, timeout_ms=cfg.timeout_ms)

            upload_error = await _detect_upload_not_supported(page)
            if upload_error:
                cfg.screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(cfg.screenshot_path), full_page=True)
                return {
                    "status": "unsupported_file_type",
                    "url": page.url,
                    "message": upload_error,
                    "uploaded_file": str(cfg.twbx_path),
                    "debug_screenshot": str(cfg.screenshot_path),
                }

            # Give the app some time to process upload bootstrap.
            await page.wait_for_timeout(5000)

            # In web authoring, this opens the publish dialog after upload.
            await _click_first_visible(
                page,
                selectors=[
                    'button:has-text("Publish As")',
                    'button:has-text("Publish")',
                    'button:has-text("Save")',
                ],
                timeout_ms=cfg.timeout_ms,
            )

            await _try_set_workbook_name(page, cfg.workbook_name)

            await _click_first_visible(
                page,
                selectors=[
                    'button:has-text("Publish")',
                    'button:has-text("Save")',
                    '[data-testid="publish-button"]',
                ],
                timeout_ms=cfg.timeout_ms,
            )

            published = await _wait_until_published(page)
            if not published:
                cfg.screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(cfg.screenshot_path), full_page=True)
                return {
                    "status": "uncertain",
                    "url": page.url,
                    "message": "Publish confirmation not detected. Verify manually.",
                    "debug_screenshot": str(cfg.screenshot_path),
                }

            return {
                "status": "published",
                "url": page.url,
                "workbook_name": cfg.workbook_name,
                "uploaded_file": str(cfg.twbx_path),
            }
        finally:
            await context.close()
            await browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a TWBX workbook to Tableau Public via Playwright")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Config JSON path")
    parser.add_argument("--email", default=None, help="Tableau account email")
    parser.add_argument("--password", default=None, help="Tableau account password")
    parser.add_argument("--twbx", default=None, help="Path to TWBX package")
    parser.add_argument("--workbook-name", default=None, help="Workbook display name")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--timeout-ms", type=int, default=60000, help="Step timeout in milliseconds")
    parser.add_argument("--slow-mo-ms", type=int, default=200, help="Slow motion delay in ms")
    parser.add_argument("--screenshot", default=None, help="Debug screenshot path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_publish_config(args)
    result = asyncio.run(publish_with_playwright(cfg))
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
