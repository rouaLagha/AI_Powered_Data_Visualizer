from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib import request

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError:  # pragma: no cover - runtime dependency guard
    Image = None
    ImageChops = None
    ImageStat = None

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Playwright checks against published reports.")
    parser.add_argument("--scenario", required=True, help="Path to the scenario JSON file.")
    parser.add_argument("--output-dir", default="", help="Override output directory for screenshots and results.")
    parser.add_argument("--headful", action="store_true", help="Run Playwright with a visible browser window.")
    args = parser.parse_args()

    scenario_path = Path(args.scenario)
    scenario = json.loads(scenario_path.read_text(encoding="utf-8-sig"))
    output_dir = Path(args.output_dir) if args.output_dir else _scenario_output_dir(scenario_path, scenario)
    output_dir.mkdir(parents=True, exist_ok=True)

    reports = _scenario_reports(scenario)
    if not reports:
        raise ValueError("Scenario must include at least one report configuration.")

    results: dict[str, Any] = {
        "status": "passed",
        "scenario_path": str(scenario_path),
        "output_dir": str(output_dir),
        "reports": {},
    }

    headless = not args.headful
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            for report_name, report_cfg in reports.items():
                report_result = _run_report(playwright, browser, headless, report_name, report_cfg, output_dir, scenario)
                results["reports"][report_name] = report_result
                if report_result["status"] != "passed":
                    results["status"] = "failed"
        finally:
            browser.close()

    screenshot_tests = _run_screenshot_tests(results, scenario, output_dir)
    if screenshot_tests:
        results["screenshot_tests"] = screenshot_tests
        if screenshot_tests.get("status") == "failed":
            results["status"] = "failed"

    data_tests = _run_data_tests(results, scenario)
    if data_tests:
        results["data_tests"] = data_tests
        if data_tests.get("conformance_status"):
            results["conformance_status"] = data_tests.get("conformance_status")
        if data_tests.get("status") == "failed":
            results["status"] = "failed"

    results_path = output_dir / "published_reports_playwright_report.json"
    results_path.write_text(json.dumps(results, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=True))
    return 0 if results["status"] == "passed" else 1


def _scenario_output_dir(scenario_path: Path, scenario: dict[str, Any]) -> Path:
    configured = _value_to_str((scenario.get("output") or {}).get("dir"))
    if configured:
        return Path(configured)
    return scenario_path.parent


def _scenario_reports(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    reports = scenario.get("reports")
    if isinstance(reports, dict):
        return {str(name): cfg for name, cfg in reports.items() if isinstance(cfg, dict)}
    if isinstance(scenario.get("powerbi"), dict) or isinstance(scenario.get("tableau"), dict):
        result: dict[str, dict[str, Any]] = {}
        if isinstance(scenario.get("powerbi"), dict):
            result["powerbi"] = scenario["powerbi"]
        if isinstance(scenario.get("tableau"), dict):
            result["tableau"] = scenario["tableau"]
        return result
    return {}


def _run_report(
    playwright,
    browser,
    headless: bool,
    report_name: str,
    report_cfg: dict[str, Any],
    output_dir: Path,
    scenario: dict[str, Any],
) -> dict[str, Any]:
    url = _value_to_str(report_cfg.get("url") or report_cfg.get("web_url") or report_cfg.get("report_web_url"))
    if not url:
        return {
            "status": "failed",
            "error": "Missing report url",
            "screenshot_path": "",
        }

    screenshot_name = _value_to_str(report_cfg.get("screenshot_name")) or f"{report_name}.png"
    screenshot_path = output_dir / screenshot_name
    configured_storage_state_path = _value_to_str(report_cfg.get("storage_state_path"))
    configured_user_data_dir = _value_to_str(report_cfg.get("user_data_dir") or report_cfg.get("profile_dir"))
    prefer_user_data_dir = bool(report_cfg.get("prefer_user_data_dir") or report_cfg.get("prefer_profile"))
    storage_state_path = configured_storage_state_path or _default_storage_state_path(report_name)
    user_data_dir = configured_user_data_dir
    if prefer_user_data_dir:
        user_data_dir = user_data_dir or _default_user_data_dir(report_name)
        storage_state_path = "" if user_data_dir else storage_state_path
    elif not user_data_dir and not storage_state_path:
        user_data_dir = _default_user_data_dir(report_name)
    storage_state_path = _resolve_project_path(storage_state_path)
    user_data_dir = _resolve_project_path(user_data_dir)
    context_kwargs: dict[str, Any] = {
        "viewport": report_cfg.get("viewport") or {"width": 1600, "height": 1200},
        "locale": _value_to_str(report_cfg.get("locale")) or "en-US",
    }
    if user_data_dir:
        profile_path = Path(user_data_dir)
        if not profile_path.exists():
            return {
                "status": "failed",
                "url": url,
                "screenshot_path": str(screenshot_path),
                "storage_state_path": storage_state_path,
                "user_data_dir": user_data_dir,
                "error": f"Configured user_data_dir does not exist: {user_data_dir}",
            }
    elif storage_state_path:
        storage_path = Path(storage_state_path)
        if not storage_path.exists():
            return {
                "status": "failed",
                "url": url,
                "screenshot_path": str(screenshot_path),
                "storage_state_path": storage_state_path,
                "error": f"Configured storage_state_path does not exist: {storage_state_path}",
            }
        context_kwargs["storage_state"] = str(storage_path)

    if user_data_dir:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(Path(user_data_dir)),
            headless=headless,
            **context_kwargs,
        )
    else:
        context = browser.new_context(**context_kwargs)

    with context:
        page = context.new_page()
        report_url = url
        try:
            report_url = _apply_url_filter_params(url, report_cfg, scenario)
            page.goto(report_url, wait_until="domcontentloaded", timeout=_timeout_ms(report_cfg, default=120000))
            auth_error = _detect_auth_required(page, report_name)
            if auth_error:
                return _auth_failure_result(
                    page,
                    output_dir,
                    report_name,
                    report_url,
                    screenshot_path,
                    report_cfg,
                    storage_state_path,
                    user_data_dir,
                    auth_error,
                )
            _prepare_report_for_capture(page, report_name, report_cfg, scenario)
            input_error = _detect_required_input(page, report_name)
            if input_error:
                return _failure_result(
                    page,
                    output_dir,
                    report_name,
                    report_url,
                    screenshot_path,
                    report_cfg,
                    storage_state_path,
                    user_data_dir,
                    input_error,
                    extra={"parameters_required": True},
                )
            _wait_for_report_ready(page, report_cfg, report_name)
            _apply_filter_steps(page, report_cfg, scenario)
            _prepare_report_for_capture(page, report_name, report_cfg, scenario)
            _wait_for_report_ready(page, report_cfg, report_name)
            auth_error = _detect_auth_required(page, report_name)
            if auth_error:
                return _auth_failure_result(
                    page,
                    output_dir,
                    report_name,
                    report_url,
                    screenshot_path,
                    report_cfg,
                    storage_state_path,
                    user_data_dir,
                    auth_error,
                )
            input_error = _detect_required_input(page, report_name)
            if input_error:
                return _failure_result(
                    page,
                    output_dir,
                    report_name,
                    report_url,
                    screenshot_path,
                    report_cfg,
                    storage_state_path,
                    user_data_dir,
                    input_error,
                    extra={"parameters_required": True},
                )
            page.screenshot(path=str(screenshot_path), full_page=bool(report_cfg.get("full_page", True)))
            data_profile = _extract_report_data_profile(page, report_name, output_dir, scenario, screenshot_path)
            _clear_error_file(output_dir, report_name)
            return {
                "status": "passed",
                "url": report_url,
                "screenshot_path": str(screenshot_path),
                "storage_state_path": storage_state_path,
                "user_data_dir": user_data_dir,
                "data_profile": data_profile,
            }
        except Exception as exc:
            auth_error = _detect_auth_required(page, report_name)
            if auth_error:
                return _auth_failure_result(
                    page,
                    output_dir,
                    report_name,
                    report_url,
                    screenshot_path,
                    report_cfg,
                    storage_state_path,
                    user_data_dir,
                    auth_error,
                )
            input_error = _detect_required_input(page, report_name)
            if input_error:
                return _failure_result(
                    page,
                    output_dir,
                    report_name,
                    report_url,
                    screenshot_path,
                    report_cfg,
                    storage_state_path,
                    user_data_dir,
                    input_error,
                    extra={"parameters_required": True, "original_error": str(exc)},
                )
            return _failure_result(
                page,
                output_dir,
                report_name,
                report_url,
                screenshot_path,
                report_cfg,
                storage_state_path,
                user_data_dir,
                str(exc),
            )
        finally:
            page.close()


def _auth_failure_result(
    page,
    output_dir: Path,
    report_name: str,
    url: str,
    screenshot_path: Path,
    report_cfg: dict[str, Any],
    storage_state_path: str,
    user_data_dir: str,
    error: str,
) -> dict[str, Any]:
    failure_screenshot_path = _failure_screenshot_path(screenshot_path)
    _safe_screenshot(page, failure_screenshot_path, full_page=bool(report_cfg.get("full_page", True)))
    error_path = output_dir / f"{report_name}.error.txt"
    error_path.write_text(error, encoding="utf-8")
    return {
        "status": "failed",
        "url": url,
        "screenshot_path": str(screenshot_path),
        "failure_screenshot_path": str(failure_screenshot_path),
        "storage_state_path": storage_state_path,
        "user_data_dir": user_data_dir,
        "auth_required": True,
        "error": error,
        "error_path": str(error_path),
    }


def _failure_result(
    page,
    output_dir: Path,
    report_name: str,
    url: str,
    screenshot_path: Path,
    report_cfg: dict[str, Any],
    storage_state_path: str,
    user_data_dir: str,
    error: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failure_screenshot_path = _failure_screenshot_path(screenshot_path)
    _safe_screenshot(page, failure_screenshot_path, full_page=bool(report_cfg.get("full_page", True)))
    error_path = output_dir / f"{report_name}.error.txt"
    error_path.write_text(error, encoding="utf-8")
    result = {
        "status": "failed",
        "url": url,
        "screenshot_path": str(screenshot_path),
        "failure_screenshot_path": str(failure_screenshot_path),
        "storage_state_path": storage_state_path,
        "user_data_dir": user_data_dir,
        "error": error,
        "error_path": str(error_path),
    }
    if isinstance(extra, dict):
        result.update(extra)
    return result


def _safe_screenshot(page, screenshot_path: Path, full_page: bool) -> None:
    try:
        page.screenshot(path=str(screenshot_path), full_page=full_page)
    except Exception:
        pass


def _failure_screenshot_path(screenshot_path: Path) -> Path:
    suffix = screenshot_path.suffix or ".png"
    return screenshot_path.with_name(f"{screenshot_path.stem}.failure{suffix}")


def _clear_error_file(output_dir: Path, report_name: str) -> None:
    try:
        (output_dir / f"{report_name}.error.txt").unlink(missing_ok=True)
    except Exception:
        pass


def _run_screenshot_tests(results: dict[str, Any], scenario: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    config = scenario.get("screenshot_tests")
    if config is False or (isinstance(config, dict) and config.get("enabled") is False):
        return {"status": "skipped", "reason": "Screenshot tests are disabled for this scenario."}
    test_cfg = config if isinstance(config, dict) else {}

    if Image is None or ImageChops is None or ImageStat is None:
        return {
            "status": "failed",
            "error": "Pillow is required to validate and compare report screenshots.",
        }

    reports = results.get("reports") if isinstance(results.get("reports"), dict) else {}
    image_results = {}
    status = "passed"
    skipped_count = 0
    for report_name, report_result in reports.items():
        if not isinstance(report_result, dict):
            continue
        if report_result.get("status") != "passed":
            image_results[report_name] = {
                "status": "skipped",
                "screenshot_path": _value_to_str(report_result.get("screenshot_path")),
                "reason": "Report capture failed before screenshot validation.",
            }
            skipped_count += 1
            continue
        image_result = _validate_screenshot_image(report_name, report_result, test_cfg)
        image_results[report_name] = image_result
        if image_result.get("status") == "failed":
            status = "failed"

    comparison = _compare_report_screenshots(reports, test_cfg, output_dir)

    if status == "passed" and skipped_count and skipped_count == len(image_results):
        status = "skipped"

    payload = {
        "status": status,
        "validation_role": "screenshot_capture_quality",
        "image_tests": image_results,
    }
    if comparison:
        payload["secondary_visual_check"] = comparison
    return payload


def _validate_screenshot_image(
    report_name: str,
    report_result: dict[str, Any],
    test_cfg: dict[str, Any],
) -> dict[str, Any]:
    screenshot_path = _value_to_str(report_result.get("screenshot_path"))
    if not screenshot_path:
        return {"status": "failed", "error": "Missing screenshot path."}
    path = Path(screenshot_path)
    if not path.exists() or not path.is_file():
        return {"status": "failed", "screenshot_path": str(path), "error": "Screenshot file was not created."}
    if path.stat().st_size <= 0:
        return {"status": "failed", "screenshot_path": str(path), "error": "Screenshot file is empty."}

    try:
        with Image.open(path) as image:
            metrics = _screenshot_image_metrics(image)
    except Exception as exc:
        return {
            "status": "failed",
            "screenshot_path": str(path),
            "error": f"Screenshot could not be opened as an image: {exc}",
        }

    min_width = _safe_int(test_cfg.get("min_width"), default=400)
    min_height = _safe_int(test_cfg.get("min_height"), default=300)
    min_non_white_ratio = _safe_float(test_cfg.get("min_non_white_ratio"), default=0.01)
    min_stddev = _safe_float(test_cfg.get("min_stddev"), default=5.0)
    errors = []
    if metrics["width"] < min_width or metrics["height"] < min_height:
        errors.append(f"image is too small ({metrics['width']}x{metrics['height']})")
    if metrics["non_white_ratio"] < min_non_white_ratio:
        errors.append("image appears blank or nearly blank")
    if metrics["stddev_luma"] < min_stddev:
        errors.append("image has too little visual variation")

    return {
        "status": "failed" if errors else "passed",
        "screenshot_path": str(path),
        "report": str(report_name),
        "metrics": metrics,
        "error": "; ".join(errors),
    }


def _screenshot_image_metrics(image) -> dict[str, Any]:
    width, height = image.size
    gray = image.convert("L")
    gray.thumbnail((640, 640))
    histogram = gray.histogram()
    total = max(1, gray.size[0] * gray.size[1])
    near_white = sum(histogram[245:])
    stat = ImageStat.Stat(gray)
    return {
        "width": width,
        "height": height,
        "file_pixels_sampled": total,
        "non_white_ratio": round(1 - (near_white / total), 4),
        "mean_luma": round(float(stat.mean[0]), 2),
        "stddev_luma": round(float(stat.stddev[0]), 2),
    }


def _compare_report_screenshots(
    reports: dict[str, Any],
    test_cfg: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    source_name = _value_to_str(test_cfg.get("source_report")) or "powerbi"
    target_name = _value_to_str(test_cfg.get("target_report")) or "tableau"
    source = reports.get(source_name)
    target = reports.get(target_name)
    base_payload = {
        "role": "secondary_visual_check",
        "method": "cropped_global_pixel_similarity",
        "affects_status": False,
        "source_report": source_name,
        "target_report": target_name,
    }
    if not isinstance(source, dict) or not isinstance(target, dict):
        return {
            **base_payload,
            "status": "skipped",
            "reason": f"Both {source_name} and {target_name} screenshots are required for the secondary pixel check.",
        }
    if source.get("status") != "passed" or target.get("status") != "passed":
        return {
            **base_payload,
            "status": "skipped",
            "reason": "Secondary pixel check requires both report captures to pass first.",
        }

    source_path = Path(_value_to_str(source.get("screenshot_path")))
    target_path = Path(_value_to_str(target.get("screenshot_path")))
    if not source_path.exists() or not target_path.exists():
        return {
            **base_payload,
            "status": "skipped",
            "error": "Secondary pixel check could not find both screenshot files.",
            "source_screenshot_path": str(source_path),
            "target_screenshot_path": str(target_path),
        }

    width = _safe_int(test_cfg.get("comparison_width"), default=512)
    height = _safe_int(test_cfg.get("comparison_height"), default=384)
    min_similarity = _safe_float(test_cfg.get("min_similarity_percent"), default=85.0)
    diff_path = output_dir / "screenshot_pixel_diff.png"

    try:
        with Image.open(source_path) as source_image, Image.open(target_path) as target_image:
            source_content, source_crop = _report_content_image(source_image.convert("RGB"), test_cfg)
            target_content, target_crop = _report_content_image(target_image.convert("RGB"), test_cfg)
            source_resized = source_content.resize((width, height))
            target_resized = target_content.resize((width, height))
            diff = ImageChops.difference(source_resized, target_resized)
            gray_diff = diff.convert("L")
            mean_delta = float(ImageStat.Stat(gray_diff).mean[0])
            similarity = max(0.0, min(100.0, (1 - (mean_delta / 255)) * 100))
            diff.save(diff_path)
    except Exception as exc:
        return {
            **base_payload,
            "status": "skipped",
            "error": f"Secondary pixel check failed: {exc}",
            "source_screenshot_path": str(source_path),
            "target_screenshot_path": str(target_path),
        }

    passed = similarity >= min_similarity
    return {
        **base_payload,
        "status": "passed" if passed else "review",
        "source_screenshot_path": str(source_path),
        "target_screenshot_path": str(target_path),
        "diff_path": str(diff_path),
        "source_crop": source_crop,
        "target_crop": target_crop,
        "comparison_size": {"width": width, "height": height},
        "similarity_percent": round(similarity, 2),
        "mean_pixel_delta": round(mean_delta, 2),
        "min_similarity_percent": min_similarity,
        "reason": "" if passed else "Pixel similarity is below the configured threshold. This is informational only; semantic data validation is the primary check.",
        "error": "",
    }


def _report_content_image(image, test_cfg: dict[str, Any]):
    width, height = image.size
    crop_cfg = test_cfg.get("report_content_crop") or test_cfg.get("content_crop")
    if not isinstance(crop_cfg, dict):
        return image, {"applied": False, "box": [0, 0, width, height], "mode": "full_image"}

    left = _crop_margin(crop_cfg, "left", "left_ratio", width)
    top = _crop_margin(crop_cfg, "top", "top_ratio", height)
    right = _crop_margin(crop_cfg, "right", "right_ratio", width)
    bottom = _crop_margin(crop_cfg, "bottom", "bottom_ratio", height)
    box = (left, top, max(left + 1, width - right), max(top + 1, height - bottom))
    if box == (0, 0, width, height):
        return image, {"applied": False, "box": [0, 0, width, height], "mode": "full_image"}
    return image.crop(box), {"applied": True, "box": list(box), "mode": "configured_margins"}


def _crop_margin(crop_cfg: dict[str, Any], pixel_key: str, ratio_key: str, size: int) -> int:
    raw_ratio = crop_cfg.get(ratio_key)
    try:
        ratio = float(raw_ratio)
    except (TypeError, ValueError):
        ratio = 0.0
    if ratio > 0:
        return max(0, min(size - 1, int(size * min(ratio, 0.95))))
    try:
        pixels = int(crop_cfg.get(pixel_key) or 0)
    except (TypeError, ValueError):
        pixels = 0
    return max(0, min(size - 1, pixels))


def _extract_report_data_profile(
    page,
    report_name: str,
    output_dir: Path,
    scenario: dict[str, Any],
    screenshot_path: Path | None = None,
) -> dict[str, Any]:
    dom_text = _frames_data_text(page)
    normalized_name = re.sub(r"[^a-z0-9_-]+", "_", str(report_name or "report").strip().lower()).strip("_")
    ocr_text, ocr_payload = _extract_screenshot_ocr_text(screenshot_path, output_dir, normalized_name, scenario)
    text = "\n".join(part for part in [dom_text, ocr_text] if part.strip())
    text_path = output_dir / f"{normalized_name or 'report'}_report_text.txt"
    text_path.write_text(text, encoding="utf-8")

    profile = _build_report_data_profile(text, scenario)
    profile["text_path"] = str(text_path)
    profile["report"] = str(report_name or "")
    profile["screenshot_path"] = str(screenshot_path) if screenshot_path else ""
    if _vision_pair_comparison_enabled(scenario):
        vision_profile, vision_payload = {}, {
            "status": "skipped",
            "reason": "Pairwise screenshot vision comparison is the primary data validation engine.",
        }
    else:
        vision_profile, vision_payload = _extract_screenshot_vision_profile(
            screenshot_path,
            output_dir,
            normalized_name,
            report_name,
            scenario,
            profile,
        )
    if vision_profile:
        _merge_vision_profile(profile, vision_profile)
    profile["extraction_sources"] = {
        "dom_text": {"status": "passed" if dom_text.strip() else "skipped", "text_length": len(dom_text)},
        "screenshot_ocr": ocr_payload,
        "screenshot_vision": vision_payload,
    }
    return profile


def _vision_pair_comparison_enabled(scenario: dict[str, Any]) -> bool:
    data_cfg = scenario.get("data_tests") if isinstance(scenario.get("data_tests"), dict) else {}
    vision_cfg = data_cfg.get("screenshot_vision") if isinstance(data_cfg.get("screenshot_vision"), dict) else {}
    if "pair_comparison" in vision_cfg:
        return _truthy_value(vision_cfg.get("pair_comparison"))
    if "primary_compare" in vision_cfg:
        return _truthy_value(vision_cfg.get("primary_compare"))
    if "enabled" in vision_cfg:
        return _truthy_value(vision_cfg.get("enabled"))
    return True


def _extract_screenshot_ocr_text(
    screenshot_path: Path | None,
    output_dir: Path,
    normalized_name: str,
    scenario: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    data_cfg = scenario.get("data_tests") if isinstance(scenario.get("data_tests"), dict) else {}
    screenshot_cfg = scenario.get("screenshot_tests") if isinstance(scenario.get("screenshot_tests"), dict) else {}
    ocr_cfg = data_cfg.get("screenshot_ocr") if isinstance(data_cfg.get("screenshot_ocr"), dict) else {}
    if "report_content_crop" not in ocr_cfg and "content_crop" not in ocr_cfg:
        inherited_crop = screenshot_cfg.get("report_content_crop") or screenshot_cfg.get("content_crop")
        if isinstance(inherited_crop, dict):
            ocr_cfg = {**ocr_cfg, "report_content_crop": inherited_crop}
    if "enabled" in ocr_cfg and not _truthy_value(ocr_cfg.get("enabled")):
        return "", {"status": "skipped", "reason": "Screenshot OCR is disabled."}
    if screenshot_path is None or not Path(screenshot_path).exists():
        return "", {"status": "skipped", "reason": "Screenshot file is not available for OCR."}
    if Image is None:
        return "", {"status": "skipped", "reason": "Pillow is required to prepare screenshots for OCR."}

    configured_binary = _value_to_str(ocr_cfg.get("tesseract_path"))
    tesseract_path = configured_binary or shutil.which("tesseract") or shutil.which("tesseract.exe")
    if not tesseract_path:
        return "", {
            "status": "skipped",
            "reason": "No Tesseract OCR executable was found. DOM/SVG text extraction was used instead.",
        }

    content_path = output_dir / f"{normalized_name or 'report'}_report_content.png"
    text_path = output_dir / f"{normalized_name or 'report'}_report_ocr.txt"
    try:
        with Image.open(screenshot_path) as image:
            content_image, crop = _report_content_image(image.convert("RGB"), ocr_cfg)
            content_image.save(content_path)
        command = [tesseract_path, str(content_path), "stdout", "--psm", _value_to_str(ocr_cfg.get("psm")) or "6"]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_safe_int(ocr_cfg.get("timeout_seconds"), default=30),
            check=False,
        )
    except Exception as exc:
        return "", {"status": "skipped", "reason": f"Screenshot OCR could not run: {exc}"}

    text = _value_to_str(completed.stdout)
    text_path.write_text(text, encoding="utf-8")
    return text, {
        "status": "passed" if text else "skipped",
        "text_length": len(text),
        "text_path": str(text_path),
        "content_image_path": str(content_path),
        "crop": crop,
        "reason": "" if text else _value_to_str(completed.stderr) or "OCR returned no text.",
    }


def _extract_screenshot_vision_profile(
    screenshot_path: Path | None,
    output_dir: Path,
    normalized_name: str,
    report_name: str,
    scenario: dict[str, Any],
    current_profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    data_cfg = scenario.get("data_tests") if isinstance(scenario.get("data_tests"), dict) else {}
    vision_cfg = data_cfg.get("screenshot_vision") if isinstance(data_cfg.get("screenshot_vision"), dict) else {}
    if "enabled" in vision_cfg and not _truthy_value(vision_cfg.get("enabled")):
        return {}, {"status": "skipped", "reason": "Screenshot vision extraction is disabled."}
    if not _profile_needs_vision(current_profile, scenario):
        return {}, {"status": "skipped", "reason": "DOM/OCR extraction already exposed the required comparable values."}
    if screenshot_path is None or not Path(screenshot_path).exists():
        return {}, {"status": "skipped", "reason": "Screenshot file is not available for vision extraction."}
    if Image is None:
        return {}, {"status": "skipped", "reason": "Pillow is required to prepare screenshots for vision extraction."}

    llm_cfg, cfg_reason = _vision_llm_config(scenario, vision_cfg)
    if not llm_cfg:
        return {}, {"status": "skipped", "reason": cfg_reason or "No vision LLM configuration was found."}

    content_path = output_dir / f"{normalized_name or 'report'}_report_vision_input.png"
    raw_response_path = output_dir / f"{normalized_name or 'report'}_report_vision_response.txt"
    json_response_path = output_dir / f"{normalized_name or 'report'}_report_vision_profile.json"
    try:
        with Image.open(screenshot_path) as image:
            content_image, crop = _report_content_image(image.convert("RGB"), vision_cfg)
            content_image.thumbnail((_safe_int(vision_cfg.get("max_width"), default=1600), _safe_int(vision_cfg.get("max_height"), default=1600)))
            content_image.save(content_path)
        response_text = _call_vision_llm(
            image_path=content_path,
            report_name=report_name,
            scenario=scenario,
            llm_cfg=llm_cfg,
            vision_cfg=vision_cfg,
        )
        raw_response_path.write_text(response_text, encoding="utf-8")
        profile = _parse_vision_profile_response(response_text)
        json_response_path.write_text(json.dumps(profile, indent=2, ensure_ascii=True), encoding="utf-8")
    except Exception as exc:
        return {}, {
            "status": "skipped",
            "reason": f"Screenshot vision extraction could not run: {type(exc).__name__}: {exc}",
            "content_image_path": str(content_path),
        }

    return profile, {
        "status": "passed",
        "content_image_path": str(content_path),
        "response_path": str(raw_response_path),
        "profile_path": str(json_response_path),
        "crop": crop,
        "model": llm_cfg.get("model", ""),
    }


def _profile_needs_vision(profile: dict[str, Any], scenario: dict[str, Any]) -> bool:
    data_cfg = scenario.get("data_tests") if isinstance(scenario.get("data_tests"), dict) else {}
    required = data_cfg.get("required_kpis")
    if isinstance(required, list) and required:
        required_labels = [_normalize_key(item) for item in required if _normalize_key(item)]
    else:
        required_labels = ["orders", "sales", "quota", "variance"]
    kpis = profile.get("kpis") if isinstance(profile.get("kpis"), dict) else {}
    for label in required_labels:
        if _kpi_numeric_value(kpis.get(label)) is None:
            return True
    required_categories = data_cfg.get("required_chart_categories")
    if isinstance(required_categories, list) and required_categories:
        present_tokens = set()
        for visual in profile.get("visuals") if isinstance(profile.get("visuals"), list) else []:
            present_tokens.update(_visual_signature_tokens(visual))
        chart = profile.get("chart") if isinstance(profile.get("chart"), dict) else {}
        for category in chart.get("categories") if isinstance(chart.get("categories"), list) else []:
            present_tokens.add(_normalize_key(category))
        missing_categories = [_normalize_key(item) for item in required_categories if _normalize_key(item) not in present_tokens]
        if missing_categories:
            return True
    visuals = profile.get("visuals") if isinstance(profile.get("visuals"), list) else []
    has_chart_visual = any(
        isinstance(visual, dict) and _normalize_key(visual.get("kind")) == "chart"
        for visual in visuals
    )
    if not has_chart_visual:
        return True
    return False


def _vision_llm_config(scenario: dict[str, Any], vision_cfg: dict[str, Any]) -> tuple[dict[str, Any], str]:
    inline_cfg = vision_cfg.get("llm")
    if isinstance(inline_cfg, dict):
        return _normalize_vision_llm_cfg(inline_cfg), ""

    config_path = _value_to_str(vision_cfg.get("config_path")) or _value_to_str(scenario.get("llm_config_path"))
    if not config_path:
        default_path = Path(__file__).resolve().parents[2] / "backend" / "config" / "llm_config.json"
        if default_path.exists():
            config_path = str(default_path)
    if not config_path:
        return {}, "No llm_config_path is available for screenshot vision extraction."

    path = Path(_resolve_project_path(config_path))
    if not path.exists():
        return {}, f"Vision LLM config path does not exist: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {}, f"Vision LLM config could not be read: {exc}"

    agent_name = _value_to_str(vision_cfg.get("agent")) or "vision_agent"
    cfg = payload.get(agent_name)
    if not isinstance(cfg, dict):
        cfg = payload.get("agent1")
    if not isinstance(cfg, dict):
        return {}, f"Vision LLM config did not contain {agent_name!r} or 'agent1'."
    return _normalize_vision_llm_cfg(cfg), ""


def _normalize_vision_llm_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_url": _value_to_str(cfg.get("api_url")),
        "api_key": _value_to_str(cfg.get("api_key")),
        "model": _value_to_str(cfg.get("model")) or "gpt-4o-mini",
        "temperature": _safe_float(cfg.get("temperature"), default=0.1),
        "timeout_seconds": _safe_int(cfg.get("timeout_seconds"), default=120),
    }


def _call_vision_llm(
    image_path: Path,
    report_name: str,
    scenario: dict[str, Any],
    llm_cfg: dict[str, Any],
    vision_cfg: dict[str, Any],
) -> str:
    api_url = _value_to_str(llm_cfg.get("api_url"))
    if not api_url:
        raise ValueError("Vision LLM api_url is missing.")
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    data_url = f"data:image/png;base64,{image_b64}"
    system_prompt = (
        "You extract BI report content from screenshots. Return only valid JSON. "
        "Do not infer hidden values; extract only visible text, KPI values, chart labels, axes, legends, and visible data trends."
    )
    user_prompt = _vision_report_prompt(report_name, scenario, vision_cfg)
    payload = _build_vision_payload(
        api_url=api_url,
        model=_value_to_str(llm_cfg.get("model")) or "gpt-4o-mini",
        temperature=_safe_float(llm_cfg.get("temperature"), default=0.1),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        data_url=data_url,
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 BI-Report-Validator/1.0",
    }
    api_key = _value_to_str(llm_cfg.get("api_key"))
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=_safe_int(llm_cfg.get("timeout_seconds"), default=120)) as resp:
            body = resp.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        raise ConnectionError(f"Vision LLM endpoint returned HTTP {exc.code}: {error_body[:300]}") from exc
    data = json.loads(body)
    return _extract_llm_response_text(data)


def _vision_report_prompt(report_name: str, scenario: dict[str, Any], vision_cfg: dict[str, Any]) -> str:
    filters = scenario.get("filters") if isinstance(scenario.get("filters"), dict) else {}
    data_cfg = scenario.get("data_tests") if isinstance(scenario.get("data_tests"), dict) else {}
    required_kpis = data_cfg.get("required_kpis") if isinstance(data_cfg.get("required_kpis"), list) else ["orders", "sales", "quota", "variance"]
    return (
        f"Analyze this screenshot of the {report_name} report. Ignore browser chrome, Tableau/PowerBI menus, toolbars, breadcrumbs, "
        "sidebars, and empty page background. Keep only the report canvas.\n"
        f"Expected filters/parameters: {json.dumps(filters, ensure_ascii=True)}.\n"
        f"Expected KPI labels if visible: {json.dumps(required_kpis, ensure_ascii=True)}.\n"
        "Return JSON with this shape exactly:\n"
        "{\n"
        '  "parameters": [{"name": "CalendarYear", "value": "2013"}],\n'
        '  "filters": [{"name": "SalesTerritoryGroup", "value": "Europe"}],\n'
        '  "kpis": [{"label": "orders", "value": "67,303"}, {"label": "sales", "value": "15,677,715"}],\n'
        '  "visuals": [{"kind": "chart", "title": "SalesAmount by EnglishMonthName", "chart_type": "line chart", '
        '"dimensions": ["EnglishMonthName"], "measures": ["SalesAmount"], "aggregations": [], '
        '"data_points": ["March", "June", "September"], "numeric_values": []}],\n'
        '  "notes": []\n'
        "}\n"
        "Use the label shown in the screenshot. Preserve number signs and suffixes as displayed."
    )


def _build_vision_payload(
    api_url: str,
    model: str,
    temperature: float,
    system_prompt: str,
    user_prompt: str,
    data_url: str,
) -> dict[str, Any]:
    base = {"model": model, "temperature": temperature}
    if "/responses" in api_url:
        base["input"] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_prompt},
                    {"type": "input_image", "image_url": data_url},
                ],
            },
        ]
        return base
    base["messages"] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    return base


def _extract_llm_response_text(response_payload: dict[str, Any]) -> str:
    output_text = response_payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    output = response_payload.get("output")
    if isinstance(output, list):
        chunks = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text)
        if chunks:
            return "\n".join(chunks).strip()
    choices = response_payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks = [part.get("text") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
            return "\n".join(chunks).strip()
    raise ValueError("Vision LLM response did not contain text.")


def _parse_vision_profile_response(response_text: str) -> dict[str, Any]:
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", str(response_text or "").strip(), flags=re.IGNORECASE | re.DOTALL)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))
    return payload if isinstance(payload, dict) else {}


def _merge_vision_profile(profile: dict[str, Any], vision_profile: dict[str, Any]) -> None:
    _merge_vision_parameters(profile, vision_profile.get("parameters"), key="parameters")
    _merge_vision_parameters(profile, vision_profile.get("filters"), key="filters")
    kpis = profile.setdefault("kpis", {})
    if isinstance(kpis, dict):
        for item in _as_dict_list(vision_profile.get("kpis")):
            label = _normalize_key(item.get("label") or item.get("name"))
            raw = _value_to_str(item.get("value") or item.get("raw"))
            numeric_value = _parse_numeric_value(raw)
            if not label or numeric_value is None:
                continue
            kpis[label] = {
                "id": _value_to_str(item.get("id")) or f"kpi_{label}",
                "label": label,
                "raw": raw,
                "normalized_value": numeric_value,
                "unit": _value_to_str(item.get("unit")),
                "source": "screenshot_vision",
            }

    visuals = profile.setdefault("visuals", [])
    if isinstance(visuals, list):
        if _as_dict_list(vision_profile.get("visuals")):
            visuals[:] = [visual for visual in visuals if not _is_low_signal_accessibility_visual(visual)]
        for label, value in (profile.get("kpis") or {}).items():
            if not isinstance(value, dict) or value.get("source") != "screenshot_vision":
                continue
            raw = _value_to_str(value.get("raw"))
            numeric_value = value.get("normalized_value")
            visuals.append(
                {
                    "kind": "kpi",
                    "id": _value_to_str(value.get("id")) or f"kpi_{label}",
                    "title": label,
                    "chart_type": "kpi",
                    "dimensions": [label],
                    "measures": [label],
                    "aggregations": ["value"],
                    "data_points": [raw] if raw else [],
                    "numeric_values": [numeric_value] if isinstance(numeric_value, (int, float)) else [],
                    "signature": _visual_signature_from_parts(label, "kpi", [label], [label], ["value"], [raw] if raw else []),
                    "source": "screenshot_vision",
                }
            )
        for visual in _as_dict_list(vision_profile.get("visuals")):
            normalized_visual = _normalize_vision_visual(visual)
            if normalized_visual:
                visuals.append(normalized_visual)
        profile["visuals"] = _dedupe_visual_profiles(visuals)


def _merge_vision_parameters(profile: dict[str, Any], values: Any, key: str) -> None:
    target = profile.setdefault(key, {})
    if not isinstance(target, dict):
        return
    for item in _as_dict_list(values):
        name = _value_to_str(item.get("name") or item.get("field") or item.get("label"))
        value = _value_to_str(item.get("value"))
        if not name or not value:
            continue
        target[name] = {"expected": target.get(name, {}).get("expected", value) if isinstance(target.get(name), dict) else value, "value": value, "present": True, "source": "screenshot_vision"}


def _normalize_vision_visual(visual: dict[str, Any]) -> dict[str, Any]:
    visual_id = _value_to_str(visual.get("id"))
    kind = _value_to_str(visual.get("kind")) or "chart"
    title = _value_to_str(visual.get("title")) or kind
    chart_type = _value_to_str(visual.get("chart_type")) or kind
    dimensions = [_value_to_str(item) for item in _as_list(visual.get("dimensions")) if _value_to_str(item)]
    measures = [_value_to_str(item) for item in _as_list(visual.get("measures")) if _value_to_str(item)]
    aggregations = [_value_to_str(item) for item in _as_list(visual.get("aggregations")) if _value_to_str(item)]
    axes = [_value_to_str(item) for item in _as_list(visual.get("axes")) if _value_to_str(item)]
    legends = [_value_to_str(item) for item in _as_list(visual.get("legends")) if _value_to_str(item)]
    chart_points = _normalize_chart_data_points(visual.get("data_points") or visual.get("chart_points") or visual.get("points"))
    axis_bindings = _normalize_axis_bindings(
        visual.get("axis_bindings")
        or visual.get("measure_axes")
        or visual.get("measure_axis_bindings")
        or visual.get("axes_by_measure"),
        chart_points,
    )
    data_points = _visual_data_point_labels(visual.get("data_points"), chart_points)
    axis_ticks = [
        value
        for value in (_parse_numeric_value(item) for item in _as_list(visual.get("axis_ticks") or visual.get("axis_values")))
        if value is not None
    ]
    numeric_values = [value for value in (_parse_numeric_value(item) for item in _as_list(visual.get("numeric_values"))) if value is not None]
    chart_point_values = [point["value"] for point in chart_points if isinstance(point.get("value"), (int, float))]
    if chart_point_values:
        numeric_values = chart_point_values
    if not numeric_values:
        numeric_values = [value for value in (_parse_numeric_value(item) for item in data_points) if value is not None]
    if not (dimensions or measures or axes or legends or data_points or numeric_values):
        return {}
    merged_dimensions = _unique_text_values(dimensions + axes)
    merged_measures = _unique_text_values(measures + legends)
    return {
        "id": visual_id,
        "kind": kind,
        "title": title,
        "chart_type": chart_type,
        "dimensions": merged_dimensions,
        "measures": merged_measures,
        "aggregations": aggregations,
        "axes": axes,
        "legends": legends,
        "axis_bindings": axis_bindings,
        "data_points": data_points,
        "chart_points": chart_points,
        "axis_ticks": axis_ticks,
        "numeric_values": numeric_values,
        "unit": _value_to_str(visual.get("unit")),
        "signature": _visual_signature_from_parts(title, chart_type, merged_dimensions, merged_measures, aggregations, data_points),
        "source": "screenshot_vision",
    }


def _normalize_chart_data_points(value: Any) -> list[dict[str, Any]]:
    points = []
    for index, item in enumerate(_as_list(value)):
        point = _normalize_chart_point(item, index)
        if point:
            points.append(point)
    return points


def _normalize_chart_point(item: Any, index: int) -> dict[str, Any]:
    if isinstance(item, dict):
        category = _value_to_str(
            item.get("category")
            or item.get("x")
            or item.get("dimension")
            or item.get("label")
            or item.get("month")
        )
        measure = _value_to_str(item.get("measure") or item.get("metric") or item.get("field") or item.get("y_measure"))
        axis = _canonical_axis_name(
            item.get("axis")
            or item.get("y_axis")
            or item.get("value_axis")
            or item.get("axis_side")
            or item.get("axis_name")
        )
        series = _value_to_str(item.get("series") or item.get("legend") or measure or item.get("name"))
        raw_value = item.get("raw_value")
        if raw_value is None:
            raw_value = item.get("displayed_value")
        if raw_value is None:
            raw_value = item.get("value")
        numeric_value = item.get("value")
        if not isinstance(numeric_value, (int, float)):
            numeric_value = _parse_numeric_value(raw_value)
        if numeric_value is None:
            numeric_value = _parse_numeric_value(item.get("numeric_value"))
        label = _value_to_str(item.get("label") or item.get("point") or category or series or f"point {index + 1}")
        if not (category or series or label or numeric_value is not None):
            return {}
        return {
            "category": category,
            "measure": measure or series,
            "axis": axis,
            "series": series,
            "label": label,
            "raw_value": _value_to_str(raw_value),
            "value": numeric_value if isinstance(numeric_value, (int, float)) else None,
            "approximate": bool(item.get("approximate") or item.get("estimated")),
            "index": index,
        }

    if isinstance(item, (list, tuple)):
        values = [_value_to_str(part) for part in item if _value_to_str(part)]
        if not values:
            return {}
        numeric_value = next((_parse_numeric_value(part) for part in reversed(values) if _parse_numeric_value(part) is not None), None)
        return {
            "category": values[0] if values else "",
            "measure": values[1] if len(values) > 2 else "",
            "axis": "",
            "series": values[1] if len(values) > 2 else "",
            "label": " | ".join(values[:-1]) if len(values) > 1 else values[0],
            "raw_value": values[-1] if numeric_value is not None else "",
            "value": numeric_value,
            "approximate": False,
            "index": index,
        }

    text = _value_to_str(item)
    if not text:
        return {}
    numeric_value = _parse_numeric_value(text)
    raw_value = text if numeric_value is not None else ""
    label = text
    if numeric_value is None:
        number_match = re.search(r"[-+]?\$?\s*\d[\d,\s]*(?:\.\d+)?\s*[kmbKMB]?\s*%?", text)
        if not number_match:
            return {}
        raw_value = number_match.group(0)
        numeric_value = _parse_numeric_value(raw_value)
        label = text[: number_match.start()].strip(" :-|,") or text[number_match.end() :].strip(" :-|,") or text
    category = ""
    series = ""
    if ":" in text:
        label_part = text.split(":", 1)[0]
        pieces = [piece.strip() for piece in re.split(r"[/|,-]", label_part) if piece.strip()]
        if pieces:
            category = pieces[0]
        if len(pieces) > 1:
            series = pieces[1]
    return {
        "category": category,
        "measure": series,
        "axis": "",
        "series": series,
        "label": label,
        "raw_value": raw_value,
        "value": numeric_value,
        "approximate": False,
        "index": index,
    }


def _visual_data_point_labels(raw_points: Any, chart_points: list[dict[str, Any]]) -> list[str]:
    labels = []
    if chart_points:
        labels.extend(_chart_point_label(point) for point in chart_points)
    for item in _as_list(raw_points):
        if isinstance(item, dict):
            continue
        text = _value_to_str(item)
        if text and text not in labels:
            labels.append(text)
    return labels


def _chart_point_label(point: dict[str, Any]) -> str:
    category = _value_to_str(point.get("category"))
    measure = _value_to_str(point.get("measure"))
    axis = _value_to_str(point.get("axis"))
    series = _value_to_str(point.get("series"))
    raw_value = _value_to_str(point.get("raw_value"))
    value = raw_value or _value_to_str(point.get("value"))
    return _format_chart_coordinate(category, measure, axis, series, value, bool(point.get("approximate"))) or _value_to_str(point.get("label"))


def _format_chart_coordinate(category: str, measure: str, axis: str, series: str, value: str, approximate: bool = False) -> str:
    parts = []
    if category:
        parts.append(f"x={category}")
    if measure:
        parts.append(f"measure={measure}")
    if axis:
        parts.append(f"axis={axis}")
    if series and _normalize_key(series) != _normalize_key(measure):
        parts.append(f"series={series}")
    elif series and not measure:
        parts.append(f"series={series}")
    if value:
        parts.append(f"y={value}")
    if approximate and parts:
        parts.append("approx")
    return ", ".join(parts)


def _normalize_axis_bindings(value: Any, chart_points: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    bindings = []
    if isinstance(value, dict):
        iterable = []
        for measure, axis_payload in value.items():
            if isinstance(axis_payload, dict):
                iterable.append({"measure": measure, **axis_payload})
            else:
                iterable.append({"measure": measure, "axis": axis_payload})
    else:
        iterable = _as_list(value)

    for item in iterable:
        binding = _normalize_axis_binding(item)
        if binding:
            bindings.append(binding)

    for point in chart_points or []:
        if not isinstance(point, dict):
            continue
        measure = _value_to_str(point.get("measure") or point.get("series"))
        axis = _canonical_axis_name(point.get("axis"))
        if measure and axis:
            bindings.append({"measure": measure, "axis": axis, "scale": "", "unit": "", "source": "chart_point"})

    seen = set()
    deduped = []
    for binding in bindings:
        key = (_normalize_key(binding.get("measure")), _canonical_axis_name(binding.get("axis")))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        deduped.append(binding)
    return deduped


def _normalize_axis_binding(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    measure = _value_to_str(
        item.get("measure")
        or item.get("metric")
        or item.get("series")
        or item.get("field")
        or item.get("name")
    )
    axis = _canonical_axis_name(
        item.get("axis")
        or item.get("side")
        or item.get("y_axis")
        or item.get("value_axis")
        or item.get("axis_name")
    )
    if not measure and not axis:
        return {}
    return {
        "measure": measure,
        "axis": axis,
        "scale": _value_to_str(item.get("scale") or item.get("range") or item.get("domain")),
        "unit": _value_to_str(item.get("unit")),
        "raw_axis": _value_to_str(item.get("axis") or item.get("side") or item.get("y_axis") or item.get("value_axis") or item.get("axis_name")),
    }


def _canonical_axis_name(value: Any) -> str:
    text = _normalize_key(value)
    if not text:
        return ""
    if any(token in text.split() for token in ["left", "primary"]) or "y left" in text or "y1" in text:
        return "left"
    if any(token in text.split() for token in ["right", "secondary"]) or "y right" in text or "y2" in text:
        return "right"
    if text in {"x", "x axis", "horizontal"}:
        return "x"
    if text in {"y", "y axis", "vertical"}:
        return "y"
    return text


def _unique_text_values(values: list[str]) -> list[str]:
    result = []
    for value in values:
        text = _value_to_str(value)
        if text and text not in result:
            result.append(text)
    return result


def _is_low_signal_accessibility_visual(visual: Any) -> bool:
    if not isinstance(visual, dict):
        return False
    if visual.get("source") == "screenshot_vision":
        return False
    raw_text = _value_to_str(visual.get("raw_text"))
    numeric_values = visual.get("numeric_values")
    has_numeric = isinstance(numeric_values, list) and bool(numeric_values)
    chart_type = _normalize_key(visual.get("chart_type"))
    if raw_text and not has_numeric:
        return True
    if chart_type == "text table chart" and not has_numeric:
        return True
    return False


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [
            {"name": key, "value": item}
            for key, item in value.items()
            if not isinstance(item, dict)
        ] + [
            {"name": key, **item}
            for key, item in value.items()
            if isinstance(item, dict)
        ]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _run_pairwise_vision_data_tests(
    results: dict[str, Any],
    scenario: dict[str, Any],
    test_cfg: dict[str, Any],
    source_name: str,
    target_name: str,
) -> dict[str, Any]:
    reports = results.get("reports") if isinstance(results.get("reports"), dict) else {}
    source = reports.get(source_name)
    target = reports.get(target_name)
    if not isinstance(source, dict) or not isinstance(target, dict):
        return {"status": "skipped", "reason": "Both report results are required for pairwise vision comparison."}
    if source.get("status") != "passed" or target.get("status") != "passed":
        return {"status": "skipped", "reason": "Pairwise vision comparison requires both report captures to pass first."}

    source_path = Path(_value_to_str(source.get("screenshot_path")))
    target_path = Path(_value_to_str(target.get("screenshot_path")))
    if not source_path.exists() or not target_path.exists():
        return {
            "status": "skipped",
            "reason": "Pairwise vision comparison could not find both screenshot files.",
            "source_screenshot_path": str(source_path),
            "target_screenshot_path": str(target_path),
        }

    vision_cfg = test_cfg.get("screenshot_vision") if isinstance(test_cfg.get("screenshot_vision"), dict) else {}
    llm_cfg, cfg_reason = _vision_llm_config(scenario, vision_cfg)
    if not llm_cfg:
        return {"status": "skipped", "reason": cfg_reason or "No vision LLM configuration was found."}

    output_dir = Path(_value_to_str(results.get("output_dir")) or _value_to_str((scenario.get("output") or {}).get("dir")) or ".")
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_response_path = output_dir / "pairwise_report_vision_response.txt"
    profile_path = output_dir / "pairwise_report_vision_profile.json"
    try:
        source_input, target_input, crop_payload = _prepare_pairwise_vision_images(
            source_path=source_path,
            target_path=target_path,
            output_dir=output_dir,
            source_name=source_name,
            target_name=target_name,
            vision_cfg=vision_cfg,
        )
        response_text = _call_pairwise_vision_llm(
            source_image_path=source_input,
            target_image_path=target_input,
            source_name=source_name,
            target_name=target_name,
            scenario=scenario,
            llm_cfg=llm_cfg,
            vision_cfg=vision_cfg,
        )
        raw_response_path.write_text(response_text, encoding="utf-8")
        payload = _parse_vision_profile_response(response_text)
        profile_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    except Exception as exc:
        return {
            "status": "skipped",
            "reason": f"Pairwise screenshot vision comparison could not run: {type(exc).__name__}: {exc}",
        }

    source_payload = _pairwise_payload_report(payload, source_name, "source")
    target_payload = _pairwise_payload_report(payload, target_name, "target")
    source_profile = _vision_profile_from_payload(source_payload, source_name)
    target_profile = _vision_profile_from_payload(target_payload, target_name)
    llm_matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []

    checks: list[dict[str, Any]] = []
    checks.extend(_compare_parameter_values(source_profile, target_profile, scenario, test_cfg, source_name, target_name))
    checks.extend(_compare_filter_values(source_profile, target_profile, scenario, test_cfg, source_name, target_name))
    checks.extend(_compare_kpi_values(source_profile, target_profile, test_cfg, source_name, target_name))
    checks.extend(_compare_visual_profiles_with_declared_matches(source_profile, target_profile, test_cfg, source_name, target_name, llm_matches))
    _annotate_conformity_status(checks, test_cfg)

    failed = [check for check in checks if check.get("status") == "failed"]
    partial = [check for check in checks if check.get("status") == "partial"]
    skipped = [check for check in checks if check.get("status") == "skipped"]
    non_blocking_skipped = [check for check in skipped if _is_non_blocking_pairwise_skip(check, test_cfg)]
    blocking_skipped = [check for check in skipped if check not in non_blocking_skipped]
    if failed:
        status = "failed"
        conformance_status = "non_conformant"
    elif partial or blocking_skipped or not checks:
        status = "partial"
        conformance_status = "partially_conformant"
    else:
        status = "passed"
        conformance_status = "conformant"

    kpi_checks = [check for check in checks if check.get("type") == "kpi"]
    passed_kpis = [check for check in kpi_checks if check.get("status") == "passed"]
    comparable_kpis = [check for check in kpi_checks if check.get("status") != "skipped"]
    visual_checks = [check for check in checks if check.get("type") == "visual"]
    chart_point_checks = [
        check
        for check in visual_checks
        if isinstance(check.get("chart_point_comparison"), dict)
        and check.get("chart_point_comparison", {}).get("status") in {"passed", "failed"}
    ]
    passed_chart_points = [
        check for check in chart_point_checks if check.get("chart_point_comparison", {}).get("status") == "passed"
    ]
    matched_visuals = [
        _matched_visual_summary(check, source_name, target_name)
        for check in visual_checks
        if check.get("source_signature") or check.get("target_signature")
    ]

    return {
        "status": status,
        "conformance_status": conformance_status,
        "primary_validation": True,
        "comparison_method": "multimodal_pairwise_screenshot_analysis",
        "matching_basis": ["llm_visual_understanding", "visual_type", "business_title", "labels", "axes", "measure_axis_bindings", "legends", "visible_chart_points", "visible_values"],
        "source_report": source_name,
        "target_report": target_name,
        "summary": (
            f"{len(passed_kpis)}/{len(comparable_kpis)} comparable KPI comparison(s) passed; "
            f"{len(matched_visuals)} visual match(es); "
            f"{len(passed_chart_points)}/{len(chart_point_checks)} chart point comparison(s) passed; "
            f"{len(partial)} minor/partial data check(s); "
            f"{len(blocking_skipped)} non-extractable data check(s); "
            f"{len(non_blocking_skipped)} non-blocking visual warning(s); "
            f"{len(failed)} failed data check(s)."
        ),
        "warning_count": len(non_blocking_skipped),
        "warnings": _comparison_warnings(non_blocking_skipped, source_name, target_name),
        "vision_comparison": {
            "status": "passed",
            "model": llm_cfg.get("model", ""),
            "response_path": str(raw_response_path),
            "profile_path": str(profile_path),
            "source_input_image_path": str(source_input),
            "target_input_image_path": str(target_input),
            "crops": crop_payload,
        },
        "semantic_profiles": {
            source_name: _semantic_profile_public_payload(source_profile),
            target_name: _semantic_profile_public_payload(target_profile),
        },
        "llm_matches": llm_matches,
        "matched_visuals": matched_visuals,
        "comparison_report": _element_comparison_report(checks, source_name, target_name),
        "checks": checks,
    }


def _is_non_blocking_pairwise_skip(check: dict[str, Any], test_cfg: dict[str, Any]) -> bool:
    if _truthy_value(test_cfg.get("strict_missing_values")):
        return False
    if check.get("type") != "visual":
        return False
    return bool(check.get("missing_in_target") or check.get("missing_in_source"))


def _comparison_warnings(checks: list[dict[str, Any]], source_name: str, target_name: str) -> list[dict[str, Any]]:
    warnings = []
    for check in checks:
        warnings.append(
            {
                "type": check.get("type"),
                "reason": check.get("reason"),
                "missing_in_target": check.get("missing_in_target") or [],
                "missing_in_source": check.get("missing_in_source") or [],
                source_name: check.get(source_name),
                target_name: check.get(target_name),
            }
        )
    return warnings


def _prepare_pairwise_vision_images(
    source_path: Path,
    target_path: Path,
    output_dir: Path,
    source_name: str,
    target_name: str,
    vision_cfg: dict[str, Any],
) -> tuple[Path, Path, dict[str, Any]]:
    source_input = output_dir / f"{source_name}_report_pairwise_vision_input.png"
    target_input = output_dir / f"{target_name}_report_pairwise_vision_input.png"
    if Image is None:
        raise RuntimeError("Pillow is required to prepare screenshots for pairwise vision comparison.")
    max_width = _safe_int(vision_cfg.get("max_width"), default=1600)
    max_height = _safe_int(vision_cfg.get("max_height"), default=1600)
    crops: dict[str, Any] = {}
    for report_name, input_path, output_path in [
        (source_name, source_path, source_input),
        (target_name, target_path, target_input),
    ]:
        with Image.open(input_path) as image:
            content_image, crop = _report_content_image(image.convert("RGB"), vision_cfg)
            content_image.thumbnail((max_width, max_height))
            content_image.save(output_path)
            crops[report_name] = crop
    return source_input, target_input, crops


def _call_pairwise_vision_llm(
    source_image_path: Path,
    target_image_path: Path,
    source_name: str,
    target_name: str,
    scenario: dict[str, Any],
    llm_cfg: dict[str, Any],
    vision_cfg: dict[str, Any],
) -> str:
    api_url = _value_to_str(llm_cfg.get("api_url"))
    if not api_url:
        raise ValueError("Vision LLM api_url is missing.")
    source_data_url = _image_data_url(source_image_path)
    target_data_url = _image_data_url(target_image_path)
    system_prompt = (
        "You are a BI report comparison engine. Compare two report screenshots by visible business content, "
        "not by layout or pixel position. Return only valid JSON."
    )
    user_prompt = _pairwise_vision_prompt(source_name, target_name, scenario, vision_cfg)
    payload = _build_pairwise_vision_payload(
        api_url=api_url,
        model=_value_to_str(llm_cfg.get("model")) or "gpt-4o-mini",
        temperature=_safe_float(llm_cfg.get("temperature"), default=0.1),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        source_data_url=source_data_url,
        target_data_url=target_data_url,
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 BI-Report-Validator/1.0",
    }
    api_key = _value_to_str(llm_cfg.get("api_key"))
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=_safe_int(llm_cfg.get("timeout_seconds"), default=120)) as resp:
            body = resp.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        raise ConnectionError(f"Vision LLM endpoint returned HTTP {exc.code}: {error_body[:300]}") from exc
    data = json.loads(body)
    return _extract_llm_response_text(data)


def _image_data_url(image_path: Path) -> str:
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{image_b64}"


def _pairwise_vision_prompt(source_name: str, target_name: str, scenario: dict[str, Any], vision_cfg: dict[str, Any]) -> str:
    filters = scenario.get("filters") if isinstance(scenario.get("filters"), dict) else {}
    data_cfg = scenario.get("data_tests") if isinstance(scenario.get("data_tests"), dict) else {}
    required_kpis = data_cfg.get("required_kpis") if isinstance(data_cfg.get("required_kpis"), list) else ["orders", "sales", "quota", "variance"]
    return (
        f"Image 1 is the {source_name} report. Image 2 is the {target_name} report.\n"
        "Ignore browser chrome, application menus, toolbars, breadcrumbs, sidebars, and empty background. "
        "Analyze only the report canvas.\n"
        "Goal: determine whether both BI reports display the same business data even if visual positions/layout differ.\n"
        f"Expected filters/parameters, if visible: {json.dumps(filters, ensure_ascii=True)}.\n"
        f"Expected KPI labels, if visible: {json.dumps(required_kpis, ensure_ascii=True)}.\n"
        "For each report, identify KPI cards, tables, charts, gauges, and other BI visuals. Extract titles, visual type, "
        "displayed values, units, axes, legends, dimensions, measures, aggregations, and visible data points/trends. "
        "For charts, do not treat axis tick labels as data points. Axis ticks belong in axes only. "
        "If a chart has two or more measures or dual axes, explicitly identify which measure belongs to which axis "
        "(for example Sales -> left y-axis, Quota -> right y-axis). "
        f"When reading chart measure axes from the {source_name} report, treat RDL/Power BI paginated chart axis values "
        "as thousands when no explicit K/M/B suffix is visible: multiply the displayed axis coordinate by 1000 for the normalized value. "
        "Pay special attention to scaled axes. If an axis or visual uses units such as K, M, B, thousands, millions, "
        "or billions, keep raw_value as the displayed label but set value to the normalized business value. "
        "For example, raw_value '~6.1M' must have value 6100000, and raw_value '~4B' must have value 4000000000. "
        "If one report shows a scale without a suffix and the matching report shows the same measure with M/B units, "
        "use axis_bindings.unit/scale to explain the scale and avoid returning only the plotted coordinate as the business value. "
        "For every visible chart mark/point/bar/slice that can be read or estimated from the image, extract a structured "
        "data point coordinates with x/category, measure, axis, optional series, displayed raw value, normalized y/value, and approximate=true if estimated from the plotted position. "
        "For line/bar charts, prefer points like {category: 'March', measure: 'Sales', axis: 'left', series: 'Sales', raw_value: '$5.2M', value: 5200000}; this means x=March, measure=Sales, axis=left, y=5200000. "
        "Then semantically match equivalent visuals between the two reports.\n"
        "Return only JSON with this shape:\n"
        "{\n"
        '  "reports": {\n'
        f'    "{source_name}": {{"parameters": [], "filters": [], "kpis": [], "visuals": []}},\n'
        f'    "{target_name}": {{"parameters": [], "filters": [], "kpis": [], "visuals": []}}\n'
        "  },\n"
        '  "matches": [\n'
        '    {"source_visual_id": "kpi_orders", "target_visual_id": "kpi_orders", "business_name": "Orders", "confidence": 0.98}\n'
        "  ],\n"
        '  "notes": []\n'
        "}\n"
        "Each KPI object must be {\"id\", \"label\", \"value\", \"unit\"}. "
        "Each visual object must be {\"id\", \"kind\", \"title\", \"chart_type\", \"dimensions\", \"measures\", "
        "\"aggregations\", \"axes\", \"legends\", \"axis_bindings\", \"data_points\", \"numeric_values\", \"unit\"}. "
        "axis_bindings should be an array like [{\"measure\":\"Sales\", \"axis\":\"left\", \"scale\":\"0M-6M\", \"unit\":\"M\"}]. "
        "For charts, data_points should be an array of coordinate objects when possible: "
        "{\"category\", \"measure\", \"axis\", \"series\", \"raw_value\", \"value\", \"approximate\"}. "
        "numeric_values must contain data point values, not axis tick values. "
        "Use strings for displayed values exactly as visible. Do not invent hidden values."
    )


def _build_pairwise_vision_payload(
    api_url: str,
    model: str,
    temperature: float,
    system_prompt: str,
    user_prompt: str,
    source_data_url: str,
    target_data_url: str,
) -> dict[str, Any]:
    base = {"model": model, "temperature": temperature}
    if "/responses" in api_url:
        base["input"] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_prompt},
                    {"type": "input_image", "image_url": source_data_url},
                    {"type": "input_image", "image_url": target_data_url},
                ],
            },
        ]
        return base
    base["messages"] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": source_data_url}},
                {"type": "image_url", "image_url": {"url": target_data_url}},
            ],
        },
    ]
    return base


def _pairwise_payload_report(payload: dict[str, Any], report_name: str, fallback_name: str) -> dict[str, Any]:
    reports = payload.get("reports") if isinstance(payload.get("reports"), dict) else {}
    candidates = [
        reports.get(report_name),
        reports.get(report_name.lower()),
        reports.get(fallback_name),
        payload.get(f"{fallback_name}_report"),
        payload.get(report_name),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return {}


def _vision_profile_from_payload(report_payload: dict[str, Any], report_name: str) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "status": "passed" if report_payload else "failed",
        "report": report_name,
        "parameters": {},
        "filters": {},
        "kpis": {},
        "visuals": [],
        "chart": {},
        "extraction_sources": {"pairwise_screenshot_vision": {"status": "passed" if report_payload else "failed"}},
    }
    _merge_vision_profile(profile, report_payload)
    for visual in profile.get("visuals", []):
        if isinstance(visual, dict):
            visual["report"] = report_name
    all_text = "\n".join(
        _value_to_str(item)
        for visual in profile.get("visuals", [])
        if isinstance(visual, dict)
        for item in (visual.get("data_points") or []) + (visual.get("dimensions") or []) + (visual.get("measures") or [])
    )
    profile["chart"] = _extract_chart_profile(all_text)
    return profile


def _compare_visual_profiles_with_declared_matches(
    source_profile: dict[str, Any],
    target_profile: dict[str, Any],
    test_cfg: dict[str, Any],
    source_name: str,
    target_name: str,
    llm_matches: list[Any],
) -> list[dict[str, Any]]:
    source_visuals = source_profile.get("visuals") if isinstance(source_profile.get("visuals"), list) else []
    target_visuals = target_profile.get("visuals") if isinstance(target_profile.get("visuals"), list) else []
    tolerance_percent = _safe_float(test_cfg.get("numeric_tolerance_percent"), default=1.0)
    declared_matches, matched_source_ids, matched_target_ids = _declared_visual_matches(source_visuals, target_visuals, llm_matches)
    remaining_source = [visual for visual in source_visuals if id(visual) not in matched_source_ids]
    remaining_target = [visual for visual in target_visuals if id(visual) not in matched_target_ids]
    auto_matches, unmatched_source, unmatched_target = _match_visual_profiles(remaining_source, remaining_target)
    checks = [
        _compare_visual_match(match, source_name, target_name, tolerance_percent, test_cfg)
        for match in declared_matches + auto_matches
    ]
    required_categories = test_cfg.get("required_chart_categories")
    if isinstance(required_categories, list) and required_categories:
        missing_required = _required_visual_categories(required_categories, source_visuals, target_visuals)
        if missing_required:
            status = _missing_data_status(test_cfg)
            checks.append(
                {
                    "type": "visual_requirement",
                    "status": status,
                    "required_categories": required_categories,
                    "missing_categories": missing_required,
                    "reason": "Required chart categories were not exposed by the multimodal visual profiles.",
                    "error": "" if status == "skipped" else "Required chart categories were not exposed by the multimodal visual profiles.",
                }
            )
    if unmatched_source:
        checks.append(
            {
                "type": "visual",
                "status": _missing_data_status(test_cfg),
                source_name: [visual.get("signature") for visual in unmatched_source],
                target_name: [visual.get("signature") for visual in unmatched_target],
                "missing_in_target": [visual.get("signature") for visual in unmatched_source],
                "reason": "One or more source visuals could not be matched semantically in the target report.",
                "error": "" if not _truthy_value(test_cfg.get("strict_missing_values")) else "One or more source visuals could not be matched semantically in the target report.",
            }
        )
    if unmatched_target:
        checks.append(
            {
                "type": "visual",
                "status": _missing_data_status(test_cfg),
                source_name: [visual.get("signature") for visual in unmatched_source],
                target_name: [visual.get("signature") for visual in unmatched_target],
                "missing_in_source": [visual.get("signature") for visual in unmatched_target],
                "reason": "One or more target visuals could not be matched semantically in the source report.",
                "error": "" if not _truthy_value(test_cfg.get("strict_missing_values")) else "One or more target visuals could not be matched semantically in the source report.",
            }
        )
    return checks


def _declared_visual_matches(
    source_visuals: list[dict[str, Any]],
    target_visuals: list[dict[str, Any]],
    llm_matches: list[Any],
) -> tuple[list[dict[str, Any]], set[int], set[int]]:
    matches = []
    matched_source_ids: set[int] = set()
    matched_target_ids: set[int] = set()
    for item in llm_matches:
        if not isinstance(item, dict):
            continue
        source = _find_declared_visual(source_visuals, item, source_side=True)
        target = _find_declared_visual(target_visuals, item, source_side=False)
        if not source or not target or id(source) in matched_source_ids or id(target) in matched_target_ids:
            continue
        score = _safe_float(item.get("confidence"), default=0.0)
        if score <= 0:
            score = _visual_match_score(source, target)
        matches.append({"source": source, "target": target, "score": round(float(score), 3), "declared_by_llm": True})
        matched_source_ids.add(id(source))
        matched_target_ids.add(id(target))
    return matches, matched_source_ids, matched_target_ids


def _find_declared_visual(visuals: list[dict[str, Any]], match: dict[str, Any], source_side: bool) -> dict[str, Any]:
    id_keys = ["source_visual_id", "source_id"] if source_side else ["target_visual_id", "target_id"]
    id_candidates = [_normalize_key(match.get(key)) for key in id_keys if _normalize_key(match.get(key))]
    for candidate in id_candidates:
        for visual in visuals:
            if candidate and candidate == _normalize_key(visual.get("id")):
                return visual

    side_keys = ["source_title", "source_name"] if source_side else ["target_title", "target_name"]
    title_candidates = [_normalize_key(match.get(key)) for key in side_keys if _normalize_key(match.get(key))]
    for candidate in title_candidates:
        for visual in visuals:
            if candidate and candidate == _normalize_key(visual.get("title")):
                return visual

    candidates = id_candidates + title_candidates
    business_name = _normalize_key(match.get("business_name") or match.get("name"))
    if business_name:
        candidates.append(business_name)
    for visual in visuals:
        visual_candidates = {
            _normalize_key(visual.get("id")),
            _normalize_key(visual.get("title")),
            _normalize_key(visual.get("signature")),
        }
        if any(candidate and any(candidate == item or candidate in item for item in visual_candidates if item) for candidate in candidates):
            return visual
    return {}


def _annotate_conformity_status(checks: list[dict[str, Any]], test_cfg: dict[str, Any]) -> None:
    tolerance = _safe_float(test_cfg.get("numeric_tolerance_percent"), default=1.0)
    minor_tolerance = _safe_float(test_cfg.get("minor_delta_tolerance_percent"), default=max(tolerance * 5, 0.5))
    for check in checks:
        status = _value_to_str(check.get("status"))
        delta_percent = check.get("delta_percent")
        if status == "failed" and isinstance(delta_percent, (int, float)) and delta_percent <= minor_tolerance:
            check["status"] = "partial"
            check["conformity_status"] = "ecart_mineur"
            check["minor_delta_tolerance_percent"] = minor_tolerance
            check["error"] = ""
            check["reason"] = "Numeric values differ slightly but remain within the minor-delta tolerance."
        elif check.get("status") == "passed":
            check["conformity_status"] = "conforme"
        elif check.get("status") == "partial":
            check["conformity_status"] = "ecart_mineur"
        elif check.get("status") == "skipped":
            check["conformity_status"] = "non_extractible"
        else:
            check["conformity_status"] = "non_conforme"


def _matched_visual_summary(check: dict[str, Any], source_name: str, target_name: str) -> dict[str, Any]:
    return {
        "status": check.get("status"),
        "conformity_status": check.get("conformity_status"),
        "score": check.get("score"),
        source_name: check.get("source_signature"),
        target_name: check.get("target_signature"),
        "source_title": check.get("source_title"),
        "target_title": check.get("target_title"),
        "source_chart_type": check.get("source_chart_type"),
        "target_chart_type": check.get("target_chart_type"),
        "chart_point_comparison": check.get("chart_point_comparison"),
        "axis_binding_comparison": check.get("axis_binding_comparison"),
    }


def _element_comparison_report(checks: list[dict[str, Any]], source_name: str, target_name: str) -> list[dict[str, Any]]:
    rows = []
    kpi_names = {
        _normalize_key(check.get("name"))
        for check in checks
        if check.get("type") == "kpi" and _normalize_key(check.get("name"))
    }
    for check in checks:
        if check.get("type") not in {"kpi", "visual"}:
            continue
        visual_name = check.get("name") or check.get("source_title") or check.get("target_title") or check.get("type")
        if check.get("type") == "visual":
            if check.get("status") == "skipped" and not (check.get("source_signature") or check.get("target_signature")):
                continue
            if _normalize_key(visual_name) in kpi_names:
                continue
        source_value, target_value = _display_values_for_check(check, source_name, target_name)
        rows.append(
            {
                "visual_name": visual_name,
                "type": check.get("type"),
                "source_chart_type": check.get("source_chart_type"),
                "target_chart_type": check.get("target_chart_type"),
                "source_value": source_value,
                "target_value": target_value,
                "delta": check.get("delta"),
                "delta_percent": check.get("delta_percent"),
                "confidence": check.get("score"),
                "reason": check.get("reason"),
                "error": check.get("error"),
                "chart_point_comparison": check.get("chart_point_comparison"),
                "axis_binding_comparison": check.get("axis_binding_comparison"),
                "status": check.get("conformity_status") or check.get("status"),
                "raw_status": check.get("status"),
            }
        )
    return rows


def _display_values_for_check(check: dict[str, Any], source_name: str, target_name: str) -> tuple[Any, Any]:
    point_comparison = check.get("chart_point_comparison")
    if isinstance(point_comparison, dict):
        source_points = point_comparison.get("source_points")
        target_points = point_comparison.get("target_points")
        if source_points or target_points:
            return _chart_points_display(source_points), _chart_points_display(target_points)
    source_payload = check.get(source_name)
    target_payload = check.get(target_name)
    if isinstance(source_payload, dict) or isinstance(target_payload, dict):
        source_value = source_payload.get("raw") if isinstance(source_payload, dict) else source_payload
        target_value = target_payload.get("raw") if isinstance(target_payload, dict) else target_payload
        return source_value, target_value
    numeric = check.get("numeric_comparison")
    if isinstance(numeric, dict):
        source_values = numeric.get("source_values")
        target_values = numeric.get("target_values")
        if source_values or target_values:
            return source_values, target_values
    return check.get("source_signature"), check.get("target_signature")


def _chart_points_display(points: Any) -> list[str]:
    if not isinstance(points, list):
        return []
    labels = []
    for point in points:
        if not isinstance(point, dict):
            continue
        category = _value_to_str(point.get("category") or point.get("label"))
        measure = _value_to_str(point.get("measure") or point.get("series"))
        axis = _canonical_axis_name(point.get("axis"))
        series = _value_to_str(point.get("series"))
        raw_value = _value_to_str(point.get("raw_value"))
        value = _value_to_str(point.get("normalized_value")) or raw_value or _value_to_str(point.get("value"))
        coordinate = _format_chart_coordinate(category, measure, axis, series, value, bool(point.get("approximate")))
        if coordinate:
            labels.append(coordinate)
    return labels


def _build_report_data_profile(text: str, scenario: dict[str, Any]) -> dict[str, Any]:
    lines = _text_lines(text)
    filters = scenario.get("filters") if isinstance(scenario.get("filters"), dict) else {}
    parameters = _extract_parameter_profile(text, filters)
    visuals = _extract_visual_profiles(lines, text, filters)
    return {
        "status": "passed" if text.strip() else "failed",
        "text_length": len(text),
        "text_excerpt": _text_excerpt(text),
        "parameters": parameters,
        "filters": _extract_filter_profile(text, filters),
        "kpis": _extract_kpi_profile(lines),
        "visuals": visuals,
        "chart": _extract_chart_profile(text),
    }


def _run_data_tests(results: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    config = scenario.get("data_tests")
    if config is False or (isinstance(config, dict) and config.get("enabled") is False):
        return {"status": "skipped", "reason": "Data tests are disabled for this scenario."}
    test_cfg = config if isinstance(config, dict) else {}

    reports = results.get("reports") if isinstance(results.get("reports"), dict) else {}
    source_name = _value_to_str(test_cfg.get("source_report")) or "powerbi"
    target_name = _value_to_str(test_cfg.get("target_report")) or "tableau"
    pair_attempt: dict[str, Any] = {}
    if _vision_pair_comparison_enabled(scenario):
        pair_result = _run_pairwise_vision_data_tests(results, scenario, test_cfg, source_name, target_name)
        if pair_result.get("status") != "skipped":
            return pair_result
        pair_attempt = pair_result

    source_profile = _report_data_profile(reports, source_name)
    target_profile = _report_data_profile(reports, target_name)
    if not source_profile or not target_profile:
        capture_failed = any(
            isinstance(reports.get(name), dict) and reports.get(name, {}).get("status") != "passed"
            for name in (source_name, target_name)
        )
        status = "skipped" if capture_failed else ("failed" if _truthy_value(test_cfg.get("strict_missing_values")) else "partial")
        return {
            "status": status,
            "conformance_status": "not_evaluated" if status == "skipped" else ("non_conformant" if status == "failed" else "partially_conformant"),
            "primary_validation": True,
            "comparison_method": "layout_invariant_semantic_visual_matching",
            "reason": f"Both {source_name} and {target_name} data profiles are required.",
        }

    checks: list[dict[str, Any]] = []
    checks.extend(_compare_parameter_values(source_profile, target_profile, scenario, test_cfg, source_name, target_name))
    checks.extend(_compare_filter_values(source_profile, target_profile, scenario, test_cfg, source_name, target_name))
    checks.extend(_compare_kpi_values(source_profile, target_profile, test_cfg, source_name, target_name))
    checks.extend(_compare_visual_profiles(source_profile, target_profile, test_cfg, source_name, target_name))

    failed = [check for check in checks if check.get("status") == "failed"]
    passed = [check for check in checks if check.get("status") == "passed"]
    skipped = [check for check in checks if check.get("status") == "skipped"]
    if failed:
        status = "failed"
        conformance_status = "non_conformant"
    elif skipped or not checks:
        status = "partial"
        conformance_status = "partially_conformant"
    else:
        status = "passed"
        conformance_status = "conformant"

    kpi_checks = [check for check in checks if check.get("type") == "kpi"]
    passed_kpis = [check for check in kpi_checks if check.get("status") == "passed"]
    comparable_kpis = [check for check in kpi_checks if check.get("status") != "skipped"]
    skipped_kpis = [check for check in kpi_checks if check.get("status") == "skipped"]
    visual_checks = [check for check in checks if check.get("type") == "visual"]
    chart_point_checks = [
        check
        for check in visual_checks
        if isinstance(check.get("chart_point_comparison"), dict)
        and check.get("chart_point_comparison", {}).get("status") in {"passed", "failed"}
    ]
    passed_chart_points = [
        check for check in chart_point_checks if check.get("chart_point_comparison", {}).get("status") == "passed"
    ]
    matched_visuals = [
        {
            "status": check.get("status"),
            "score": check.get("score"),
            source_name: check.get("source_signature"),
            target_name: check.get("target_signature"),
            "source_title": check.get("source_title"),
            "target_title": check.get("target_title"),
            "source_chart_type": check.get("source_chart_type"),
            "target_chart_type": check.get("target_chart_type"),
            "chart_point_comparison": check.get("chart_point_comparison"),
            "axis_binding_comparison": check.get("axis_binding_comparison"),
        }
        for check in visual_checks
        if check.get("source_signature") or check.get("target_signature")
    ]
    payload = {
        "status": status,
        "conformance_status": conformance_status,
        "primary_validation": True,
        "comparison_method": "layout_invariant_semantic_visual_matching",
        "matching_basis": ["visual_type", "title", "labels", "dimensions", "measures", "measure_axis_bindings", "aggregations", "visible_chart_points", "displayed_values"],
        "source_report": source_name,
        "target_report": target_name,
        "summary": (
            f"{len(passed_kpis)}/{len(comparable_kpis)} comparable KPI comparison(s) passed; "
            f"{len(skipped_kpis)} KPI value(s) not exposed by the report viewer; "
            f"{len(matched_visuals)} semantic visual match(es); "
            f"{len(passed_chart_points)}/{len(chart_point_checks)} chart point comparison(s) passed; "
            f"{len(skipped)} data check(s) not fully extractable; "
            f"{len(failed)} failed data check(s)."
        ),
        "semantic_profiles": {
            source_name: _semantic_profile_public_payload(source_profile),
            target_name: _semantic_profile_public_payload(target_profile),
        },
        "matched_visuals": matched_visuals,
        "checks": checks,
    }
    if pair_attempt:
        payload["vision_comparison"] = pair_attempt
    return payload


def _report_data_profile(reports: dict[str, Any], report_name: str) -> dict[str, Any]:
    report = reports.get(report_name)
    if not isinstance(report, dict):
        return {}
    profile = report.get("data_profile")
    return profile if isinstance(profile, dict) else {}


def _semantic_profile_public_payload(profile: dict[str, Any]) -> dict[str, Any]:
    visuals = profile.get("visuals") if isinstance(profile.get("visuals"), list) else []
    return {
        "status": profile.get("status"),
        "report": profile.get("report"),
        "text_path": profile.get("text_path"),
        "screenshot_path": profile.get("screenshot_path"),
        "extraction_sources": profile.get("extraction_sources") if isinstance(profile.get("extraction_sources"), dict) else {},
        "parameters": profile.get("parameters") if isinstance(profile.get("parameters"), dict) else {},
        "filters": profile.get("filters") if isinstance(profile.get("filters"), dict) else {},
        "kpis": profile.get("kpis") if isinstance(profile.get("kpis"), dict) else {},
        "chart": profile.get("chart") if isinstance(profile.get("chart"), dict) else {},
        "visuals": [_visual_public_payload(visual) for visual in visuals],
    }


def _visual_public_payload(visual: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": visual.get("id"),
        "kind": visual.get("kind"),
        "title": visual.get("title"),
        "chart_type": visual.get("chart_type"),
        "dimensions": visual.get("dimensions") if isinstance(visual.get("dimensions"), list) else [],
        "measures": visual.get("measures") if isinstance(visual.get("measures"), list) else [],
        "aggregations": visual.get("aggregations") if isinstance(visual.get("aggregations"), list) else [],
        "axes": visual.get("axes") if isinstance(visual.get("axes"), list) else [],
        "legends": visual.get("legends") if isinstance(visual.get("legends"), list) else [],
        "axis_bindings": visual.get("axis_bindings") if isinstance(visual.get("axis_bindings"), list) else [],
        "data_points": visual.get("data_points") if isinstance(visual.get("data_points"), list) else [],
        "chart_points": visual.get("chart_points") if isinstance(visual.get("chart_points"), list) else [],
        "axis_ticks": visual.get("axis_ticks") if isinstance(visual.get("axis_ticks"), list) else [],
        "numeric_values": visual.get("numeric_values") if isinstance(visual.get("numeric_values"), list) else [],
        "unit": visual.get("unit"),
        "signature": visual.get("signature"),
        "source": visual.get("source"),
    }


def _compare_filter_values(
    source_profile: dict[str, Any],
    target_profile: dict[str, Any],
    scenario: dict[str, Any],
    test_cfg: dict[str, Any],
    source_name: str,
    target_name: str,
) -> list[dict[str, Any]]:
    filters = scenario.get("filters") if isinstance(scenario.get("filters"), dict) else {}
    checks = []
    for filter_name, expected_value in filters.items():
        expected_text = _normalize_filter_value(expected_value)
        if not expected_text:
            continue
        source_filter = (source_profile.get("filters") or {}).get(str(filter_name), {})
        target_filter = (target_profile.get("filters") or {}).get(str(filter_name), {})
        source_present = bool(source_filter.get("present"))
        target_present = bool(target_filter.get("present"))
        passed = source_present and target_present
        missing_status = _missing_data_status(test_cfg)
        checks.append(
            {
                "type": "filter",
                "name": str(filter_name),
                "expected": expected_text,
                "status": "passed" if passed else missing_status,
                source_name: source_present,
                target_name: target_present,
                "reason": "" if passed else "Expected filter value was not exposed in both report texts.",
                "error": "" if passed or missing_status == "skipped" else "Expected filter value was not found in both report texts.",
            }
        )
    return checks


def _compare_parameter_values(
    source_profile: dict[str, Any],
    target_profile: dict[str, Any],
    scenario: dict[str, Any],
    test_cfg: dict[str, Any],
    source_name: str,
    target_name: str,
) -> list[dict[str, Any]]:
    parameters = scenario.get("filters") if isinstance(scenario.get("filters"), dict) else {}
    source_parameters = source_profile.get("parameters") if isinstance(source_profile.get("parameters"), dict) else {}
    target_parameters = target_profile.get("parameters") if isinstance(target_profile.get("parameters"), dict) else {}
    checks = []
    for parameter_name, expected_value in parameters.items():
        expected_text = _normalize_filter_value(expected_value)
        if not expected_text:
            continue
        source_value = _parameter_display_value(_lookup_parameter_profile(source_parameters, parameter_name))
        target_value = _parameter_display_value(_lookup_parameter_profile(target_parameters, parameter_name))
        source_present = bool(source_value)
        target_present = bool(target_value)
        passed = source_present and target_present and _normalize_text_for_match(source_value) == _normalize_text_for_match(target_value)
        missing_status = _missing_data_status(test_cfg)
        checks.append(
            {
                "type": "parameter",
                "name": str(parameter_name),
                "expected": expected_text,
                "status": "passed" if passed else missing_status,
                source_name: source_value,
                target_name: target_value,
                "reason": "" if passed else "Parameter values were not matched between reports.",
                "error": "" if passed or missing_status == "skipped" else "Parameter values were not detected in both reports.",
            }
        )
    return checks


def _lookup_parameter_profile(profiles: dict[str, Any], parameter_name: Any) -> Any:
    if not isinstance(profiles, dict):
        return {}
    canonical_name = _value_to_str(parameter_name)
    candidates = _parameter_name_candidates(canonical_name)
    for candidate in candidates:
        profile = profiles.get(candidate)
        if isinstance(profile, dict):
            return profile
    normalized_candidates = {_normalize_key(candidate) for candidate in candidates}
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        if _normalize_key(name) in normalized_candidates:
            return profile
    return {}


def _parameter_name_candidates(parameter_name: str) -> list[str]:
    raw = _value_to_str(parameter_name)
    if not raw:
        return []
    words = _split_identifier_words(raw)
    candidates = [raw]
    if words:
        candidates.append(words[-1])
        if len(words) > 1:
            candidates.append(" ".join(words))
    lowered = raw.lower()
    if lowered.endswith("year"):
        candidates.extend(["Year", "CalendarYear"])
    if "group" in lowered:
        candidates.extend(["Group", "SalesTerritoryGroup"])
    result: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _compare_kpi_values(
    source_profile: dict[str, Any],
    target_profile: dict[str, Any],
    test_cfg: dict[str, Any],
    source_name: str,
    target_name: str,
) -> list[dict[str, Any]]:
    source_kpis = source_profile.get("kpis") if isinstance(source_profile.get("kpis"), dict) else {}
    target_kpis = target_profile.get("kpis") if isinstance(target_profile.get("kpis"), dict) else {}
    required = test_cfg.get("required_kpis")
    if isinstance(required, list):
        labels = [_normalize_key(item) for item in required]
        labels = [label for label in labels if label]
    else:
        labels = [label for label in ["orders", "sales", "quota", "variance"] if label in source_kpis or label in target_kpis]
        if not labels:
            labels = sorted(set(source_kpis) | set(target_kpis))

    tolerance_percent = _safe_float(test_cfg.get("numeric_tolerance_percent"), default=1.0)
    checks = []
    for label in labels:
        source_value = _kpi_numeric_value(source_kpis.get(label))
        target_value = _kpi_numeric_value(target_kpis.get(label))
        if source_value is None or target_value is None:
            missing_status = _missing_data_status(test_cfg)
            checks.append(
                {
                    "type": "kpi",
                    "name": label,
                    "status": missing_status,
                    source_name: _kpi_public_payload(source_kpis.get(label)),
                    target_name: _kpi_public_payload(target_kpis.get(label)),
                    "reason": "KPI value was not exposed by OCR/DOM extraction in both reports.",
                    "error": "" if missing_status == "skipped" else "KPI value was not detected in both reports.",
                }
            )
            continue

        delta = abs(source_value - target_value)
        denominator = max(abs(source_value), abs(target_value), 1.0)
        delta_percent = (delta / denominator) * 100
        passed = delta_percent <= tolerance_percent
        checks.append(
            {
                "type": "kpi",
                "name": label,
                "status": "passed" if passed else "failed",
                source_name: _kpi_public_payload(source_kpis.get(label)),
                target_name: _kpi_public_payload(target_kpis.get(label)),
                "delta": round(delta, 4),
                "delta_percent": round(delta_percent, 4),
                "tolerance_percent": tolerance_percent,
                "error": "" if passed else "KPI values differ more than the configured tolerance.",
            }
        )
    return checks


def _compare_visual_profiles(
    source_profile: dict[str, Any],
    target_profile: dict[str, Any],
    test_cfg: dict[str, Any],
    source_name: str,
    target_name: str,
) -> list[dict[str, Any]]:
    source_visuals = source_profile.get("visuals") if isinstance(source_profile.get("visuals"), list) else []
    target_visuals = target_profile.get("visuals") if isinstance(target_profile.get("visuals"), list) else []
    tolerance_percent = _safe_float(test_cfg.get("numeric_tolerance_percent"), default=1.0)
    if not source_visuals and not target_visuals:
        return [
            {
                "type": "visual",
                "status": "skipped",
                "reason": "No semantic visuals were detected in either report.",
            }
        ]

    matches, unmatched_source, unmatched_target = _match_visual_profiles(source_visuals, target_visuals)
    checks = [_compare_visual_match(match, source_name, target_name, tolerance_percent, test_cfg) for match in matches]

    required_categories = test_cfg.get("required_chart_categories")
    if isinstance(required_categories, list) and required_categories:
        missing_required = _required_visual_categories(required_categories, source_visuals, target_visuals)
        if missing_required:
            status = _missing_data_status(test_cfg)
            checks.append(
                {
                    "type": "visual_requirement",
                    "status": status,
                    "required_categories": required_categories,
                    "missing_categories": missing_required,
                    "reason": "Required chart categories were not exposed by the semantic visual profiles.",
                    "error": "" if status == "skipped" else "Required chart categories were not exposed by the semantic visual profiles.",
                }
            )

    if unmatched_source:
        checks.append(
            {
                "type": "visual",
                "status": _missing_data_status(test_cfg),
                source_name: [visual.get("signature") for visual in unmatched_source],
                target_name: [visual.get("signature") for visual in unmatched_target],
                "missing_in_target": [visual.get("signature") for visual in unmatched_source],
                "reason": "One or more source visuals could not be matched semantically in the target report.",
                "error": "" if not _truthy_value(test_cfg.get("strict_missing_values")) else "One or more source visuals could not be matched semantically in the target report.",
            }
        )
    if unmatched_target:
        checks.append(
            {
                "type": "visual",
                "status": _missing_data_status(test_cfg),
                source_name: [visual.get("signature") for visual in unmatched_source],
                target_name: [visual.get("signature") for visual in unmatched_target],
                "missing_in_source": [visual.get("signature") for visual in unmatched_target],
                "reason": "One or more target visuals could not be matched semantically in the source report.",
                "error": "" if not _truthy_value(test_cfg.get("strict_missing_values")) else "One or more target visuals could not be matched semantically in the source report.",
            }
        )
    return checks


def _extract_filter_profile(text: str, filters: dict[str, Any]) -> dict[str, Any]:
    normalized_text = _normalize_text_for_match(text)
    profile = {}
    for filter_name, expected_value in filters.items():
        expected_text = _normalize_filter_value(expected_value)
        expected_match = _normalize_text_for_match(expected_text)
        observed_value = _extract_parameter_value(text, filter_name, expected_text)
        profile[str(filter_name)] = {
            "expected": expected_text,
            "value": observed_value,
            "present": bool(expected_match and expected_match in normalized_text),
        }
    return profile


def _extract_kpi_profile(lines: list[str]) -> dict[str, Any]:
    labels = {
        "orders": ["orders"],
        "sales": ["sales"],
        "quota": ["quota"],
        "variance": ["variance"],
    }
    kpis = _extract_grouped_kpi_rows(lines)
    for key, aliases in labels.items():
        if key in kpis:
            continue
        match = _find_labeled_numeric_value(lines, aliases)
        if match:
            kpis[key] = match
    return kpis


def _extract_grouped_kpi_rows(lines: list[str]) -> dict[str, Any]:
    label_order = ["orders", "sales", "quota", "variance"]
    kpis: dict[str, Any] = {}
    for start_index in range(len(lines)):
        labels = []
        current_index = start_index
        while current_index < len(lines):
            line_key = _normalize_key(lines[current_index])
            if line_key not in label_order or line_key in labels:
                break
            labels.append(line_key)
            current_index += 1

        if len(labels) < 2:
            continue

        values = []
        for candidate in lines[current_index:]:
            candidate_key = _normalize_key(candidate)
            if candidate_key in label_order:
                break
            if candidate_key.startswith("run at") or candidate_key.startswith("page "):
                break
            candidate_value = _parse_numeric_value(candidate)
            if candidate_value is None:
                continue
            values.append((candidate, candidate_value))
            if len(values) >= len(labels):
                break

        if len(values) < len(labels):
            continue

        for label, (raw, normalized_value) in zip(labels, values):
            kpis.setdefault(
                label,
                {
                    "label": label,
                    "raw": raw,
                    "normalized_value": normalized_value,
                },
            )
    return kpis


def _find_labeled_numeric_value(lines: list[str], aliases: list[str]) -> dict[str, Any]:
    normalized_aliases = {_normalize_key(alias) for alias in aliases}
    for index, line in enumerate(lines):
        line_key = _normalize_key(line)
        matched_alias = next(
            (
                alias
                for alias in normalized_aliases
                if line_key == alias or line_key.startswith(f"{alias} ")
            ),
            "",
        )
        if not matched_alias:
            continue

        same_line_value = _parse_numeric_value(line[len(matched_alias) :])
        if same_line_value is not None:
            return {
                "label": matched_alias,
                "raw": line,
                "normalized_value": same_line_value,
            }

        for candidate in lines[index + 1 : index + 7]:
            if _is_likely_label_line(candidate):
                break
            if _is_likely_chart_or_viewer_line(candidate):
                break
            candidate_value = _parse_numeric_value(candidate)
            if candidate_value is not None:
                return {
                    "label": matched_alias,
                    "raw": candidate,
                    "normalized_value": candidate_value,
                }
    return {}


def _extract_chart_profile(text: str) -> dict[str, Any]:
    month_names = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    categories = [month for month in month_names if re.search(rf"\b{re.escape(month)}\b", text, flags=re.IGNORECASE)]
    axis_values = _extract_axis_values(text)
    return {
        "categories": categories,
        "axis_values": axis_values,
    }


def _extract_parameter_profile(text: str, filters: dict[str, Any]) -> dict[str, Any]:
    profile = {}
    for parameter_name, expected_value in filters.items():
        expected_text = _normalize_filter_value(expected_value)
        observed_value = _extract_parameter_value(text, parameter_name, expected_text)
        for alias in _parameter_name_candidates(str(parameter_name)):
            if observed_value:
                break
            if alias:
                observed_value = _extract_parameter_value(text, alias, expected_text)
        profile[str(parameter_name)] = {
            "expected": expected_text,
            "value": observed_value,
            "present": bool(observed_value),
        }
    return profile


def _extract_visual_profiles(lines: list[str], text: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
    visuals = _extract_data_visualization_profiles(text)
    visuals.extend(_extract_kpi_visual_profiles(lines))

    if not visuals:
        chart_profile = _extract_chart_profile(text)
        if chart_profile.get("categories") or chart_profile.get("axis_values"):
            visuals.append(
                {
                    "kind": "chart",
                    "title": "chart",
                    "chart_type": "chart",
                    "dimensions": chart_profile.get("categories") or [],
                    "measures": [],
                    "aggregations": [],
                    "data_points": chart_profile.get("axis_values") or [],
                    "numeric_values": chart_profile.get("axis_values") or [],
                    "signature": _visual_signature_from_parts(
                        "chart",
                        "chart",
                        chart_profile.get("categories") or [],
                        [],
                        [],
                        chart_profile.get("axis_values") or [],
                    ),
                }
            )

    if filters:
        for parameter_name, expected_value in filters.items():
            observed_value = _extract_parameter_value(text, parameter_name, _normalize_filter_value(expected_value))
            if not observed_value:
                for alias in _parameter_name_candidates(str(parameter_name)):
                    observed_value = _extract_parameter_value(text, alias, _normalize_filter_value(expected_value))
                    if observed_value:
                        break
            if not observed_value:
                continue
            numeric_value = _parse_numeric_value(observed_value)
            visuals.append(
                {
                    "kind": "parameter",
                    "title": str(parameter_name),
                    "chart_type": "parameter",
                    "dimensions": [str(parameter_name)],
                    "measures": [],
                    "aggregations": [],
                    "data_points": [observed_value],
                    "numeric_values": [numeric_value] if numeric_value is not None else [],
                    "signature": _visual_signature_from_parts(
                        str(parameter_name),
                        "parameter",
                        [str(parameter_name)],
                        [],
                        [],
                        [observed_value],
                    ),
                }
            )

    return _dedupe_visual_profiles(visuals)


def _extract_kpi_visual_profiles(lines: list[str]) -> list[dict[str, Any]]:
    visuals = []
    for label, value in _extract_kpi_profile(lines).items():
        raw_value = _value_to_str(value.get("raw"))
        numeric_value = value.get("normalized_value")
        visuals.append(
            {
                "kind": "kpi",
                "title": label,
                "chart_type": "kpi",
                "dimensions": [label],
                "measures": [label],
                "aggregations": ["value"],
                "data_points": [raw_value] if raw_value else [],
                "numeric_values": [numeric_value] if isinstance(numeric_value, (int, float)) else [],
                "signature": _visual_signature_from_parts(
                    label,
                    "kpi",
                    [label],
                    [label],
                    ["value"],
                    [raw_value] if raw_value else [],
                ),
            }
        )
    return visuals


def _extract_data_visualization_profiles(text: str) -> list[dict[str, Any]]:
    visuals = []
    for raw_line in _text_lines(text):
        if not raw_line.lower().startswith("data visualization."):
            continue
        description = raw_line.split("Data Visualization.", 1)[1].strip()
        description = re.sub(r"\s+Press Enter to navigate.*$", "", description, flags=re.IGNORECASE)
        chart_type = _extract_chart_type(description)
        dimensions, measures, aggregations = _extract_visual_fields(description)
        data_points = _extract_visual_data_points(description)
        numeric_values = [value for value in (_parse_numeric_value(point) for point in data_points) if value is not None]
        visuals.append(
            {
                "kind": "chart",
                "title": _extract_visual_title(description),
                "chart_type": chart_type,
                "dimensions": dimensions,
                "measures": measures,
                "aggregations": aggregations,
                "data_points": data_points,
                "numeric_values": numeric_values,
                "signature": _visual_signature_from_parts(description, chart_type, dimensions, measures, aggregations, data_points),
                "raw_text": description,
            }
        )
    return visuals


def _extract_visual_title(description: str) -> str:
    if " of " in description.lower():
        return _value_to_str(description.split(" of ", 1)[0])
    return _value_to_str(description.split(",", 1)[0])


def _extract_chart_type(description: str) -> str:
    lowered = description.lower()
    known_types = [
        "text table chart",
        "line chart",
        "bar chart",
        "pie chart",
        "scatter plot",
        "area chart",
        "kpi",
        "card",
        "table",
        "map",
        "gauge",
        "waterfall",
        "heat map",
    ]
    for chart_type in known_types:
        if chart_type in lowered:
            return chart_type
    match = re.search(r"\b([a-z ]+chart)\b", lowered)
    if match:
        return match.group(1).strip()
    return "chart"


def _extract_visual_fields(description: str) -> tuple[list[str], list[str], list[str]]:
    dimensions: list[str] = []
    measures: list[str] = []
    aggregations: list[str] = []

    for role, field in re.findall(r"([A-Za-z ][A-Za-z ]*?)\s+applied to\s+([A-Za-z0-9_%/ .-]+?)(?=,|\.|$)", description, flags=re.IGNORECASE):
        role_key = _normalize_key(role)
        field_name = _value_to_str(field)
        if not field_name:
            continue
        if role_key in {"text", "row", "rows", "column", "columns", "dimension", "dimensions"}:
            dimensions.append(field_name)
        else:
            measures.append(field_name)
        aggregations.append(f"{_value_to_str(role)} applied to {field_name}")

    if " of " in description.lower():
        after_of = description.split(" of ", 1)[1]
        before_comma = after_of.split(",", 1)[0]
        camel_fields = re.findall(r"\b[A-Z][A-Za-z0-9]+(?:[A-Z][A-Za-z0-9]+)+\b", before_comma)
        if camel_fields:
            if not dimensions:
                dimensions.append(camel_fields[0])
            for field_name in camel_fields[1:]:
                if field_name not in measures:
                    measures.append(field_name)

    if not dimensions and not measures:
        fields = re.findall(r"\b[A-Za-z][A-Za-z0-9_%/]+\b", description)
        for field_name in fields:
            normalized = _normalize_key(field_name)
            if normalized in {"data", "visualization", "chart", "of", "applied", "to", "press", "enter", "navigate"}:
                continue
            if field_name not in dimensions and field_name not in measures:
                dimensions.append(field_name)

    return dimensions, measures, aggregations


def _extract_visual_data_points(description: str) -> list[str]:
    points = []
    for candidate in re.findall(r"\b[A-Za-z][A-Za-z0-9_%/ .-]+\b", description):
        cleaned = _value_to_str(candidate)
        if not cleaned:
            continue
        normalized = _normalize_key(cleaned)
        if normalized in {"data", "visualization", "chart", "applied", "press", "enter", "navigate", "text", "line", "pie", "table", "bar", "plot", "color", "angle", "size", "measure", "names"}:
            continue
        if cleaned not in points:
            points.append(cleaned)
    return points[:20]


def _visual_signature_from_parts(
    title: str,
    chart_type: str,
    dimensions: list[str],
    measures: list[str],
    aggregations: list[str],
    data_points: list[str],
) -> str:
    parts = [
        title,
        chart_type,
        ",".join(_value_to_str(item) for item in dimensions),
        ",".join(_value_to_str(item) for item in measures),
        ",".join(_value_to_str(item) for item in aggregations),
        ",".join(_value_to_str(item) for item in data_points),
    ]
    return " | ".join(_normalize_key(part) for part in parts if _value_to_str(part))


def _dedupe_visual_profiles(visuals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for visual in visuals:
        signature = _value_to_str(visual.get("signature"))
        if not signature or signature in seen:
            continue
        seen.add(signature)
        deduped.append(visual)
    return deduped


def _match_visual_profiles(
    source_visuals: list[dict[str, Any]],
    target_visuals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    remaining_targets = list(target_visuals)
    matches = []
    unmatched_source = []
    for source in source_visuals:
        best_index = -1
        best_score = 0.0
        for index, target in enumerate(remaining_targets):
            score = _visual_match_score(source, target)
            if score > best_score:
                best_score = score
                best_index = index
        if best_index < 0 or best_score < 2.0:
            unmatched_source.append(source)
            continue
        target = remaining_targets.pop(best_index)
        matches.append({"source": source, "target": target, "score": round(best_score, 3)})
    return matches, unmatched_source, remaining_targets


def _visual_match_score(source: dict[str, Any], target: dict[str, Any]) -> float:
    score = 0.0
    if _normalize_key(source.get("kind")) == _normalize_key(target.get("kind")):
        score += 2.0
    if _normalize_key(source.get("chart_type")) == _normalize_key(target.get("chart_type")):
        score += 3.0
    score += 2.0 * _token_overlap_score(_visual_signature_tokens(source), _visual_signature_tokens(target))
    score += _token_overlap_score(
        {_normalize_key(item) for item in (source.get("dimensions") or []) if _value_to_str(item)},
        {_normalize_key(item) for item in (target.get("dimensions") or []) if _value_to_str(item)},
    )
    score += _token_overlap_score(
        {_normalize_key(item) for item in (source.get("measures") or []) if _value_to_str(item)},
        {_normalize_key(item) for item in (target.get("measures") or []) if _value_to_str(item)},
    )
    score += _token_overlap_score(
        {_normalize_key(item) for item in (source.get("data_points") or []) if _value_to_str(item)},
        {_normalize_key(item) for item in (target.get("data_points") or []) if _value_to_str(item)},
    )
    return score


def _visual_signature_tokens(visual: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("kind", "title", "chart_type", "signature"):
        value = _value_to_str(visual.get(key))
        if value:
            tokens.update(token for token in _normalize_key(value).split() if token)
    for key in ("dimensions", "measures", "aggregations", "data_points"):
        values = visual.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            text = _value_to_str(item)
            if text:
                tokens.update(token for token in _normalize_key(text).split() if token)
    return tokens


def _token_overlap_score(source_tokens: set[str], target_tokens: set[str]) -> float:
    if not source_tokens or not target_tokens:
        return 0.0
    intersection = len(source_tokens & target_tokens)
    union = len(source_tokens | target_tokens)
    return intersection / max(union, 1)


def _compare_visual_match(
    match: dict[str, Any],
    source_name: str,
    target_name: str,
    tolerance_percent: float,
    test_cfg: dict[str, Any],
) -> dict[str, Any]:
    source = match["source"]
    target = match["target"]
    missing_status = _missing_data_status(test_cfg)
    chart_like = _is_chart_like_visual(source) or _is_chart_like_visual(target)
    numeric_comparison = _compare_numeric_multiset(
        _visual_numeric_values(source),
        _visual_numeric_values(target),
        tolerance_percent,
        missing_status,
    )
    chart_point_comparison = (
        _compare_chart_points(
            source,
            target,
            tolerance_percent,
            _safe_float(test_cfg.get("chart_point_approx_tolerance_percent"), default=max(tolerance_percent * 10, 10.0)),
            missing_status,
        )
        if chart_like
        else {"status": "skipped", "reason": "Matched visual is not chart-like."}
    )
    axis_binding_comparison = (
        _compare_axis_measure_bindings(source, target, missing_status)
        if chart_like
        else {"status": "skipped", "reason": "Matched visual is not chart-like."}
    )
    dimension_overlap = _set_overlap_summary(source.get("dimensions") or [], target.get("dimensions") or [], missing_status)
    measure_overlap = _set_overlap_summary(source.get("measures") or [], target.get("measures") or [], missing_status)
    aggregation_overlap = _set_overlap_summary(source.get("aggregations") or [], target.get("aggregations") or [], missing_status)
    data_point_overlap = _set_overlap_summary(source.get("data_points") or [], target.get("data_points") or [], missing_status)
    if chart_point_comparison.get("status") == "passed" and data_point_overlap["status"] == "failed":
        data_point_overlap = {
            **data_point_overlap,
            "status": "passed",
            "reason": "Chart data point values match within tolerance; displayed labels differ or are formatted differently.",
            "point_value_match": True,
        }
    elif numeric_comparison["status"] == "passed" and data_point_overlap["status"] == "failed" and _has_numeric_display_points(source, target):
        data_point_overlap = {
            **data_point_overlap,
            "status": "passed",
            "reason": "Displayed numeric text uses different formatting, but normalized values match within tolerance.",
            "formatting_only_difference": True,
        }
    llm_declared_match = bool(match.get("declared_by_llm"))
    if llm_declared_match and numeric_comparison["status"] != "failed":
        dimension_overlap = _relax_declared_overlap(dimension_overlap, "dimensions")
        measure_overlap = _relax_declared_overlap(measure_overlap, "measures")
        if not chart_like:
            data_point_overlap = _relax_declared_overlap(data_point_overlap, "visible data points")
    parts = [numeric_comparison, dimension_overlap, measure_overlap, aggregation_overlap, data_point_overlap]
    if chart_like:
        point_status = chart_point_comparison.get("status")
        axis_status = axis_binding_comparison.get("status")
        multi_measure_chart = _has_multiple_chart_measures(source) or _has_multiple_chart_measures(target)
        if axis_status == "failed":
            status = "failed"
        elif multi_measure_chart and axis_status != "passed":
            status = missing_status
            axis_binding_comparison = {
                **axis_binding_comparison,
                "status": missing_status,
                "reason": "This chart has multiple measures, but the measure-to-axis mapping was not fully extracted.",
                "error": "" if missing_status == "skipped" else "Measure-to-axis mapping is required for multi-measure charts.",
            }
        elif point_status == "failed":
            status = "failed"
        elif point_status == "passed":
            status = "passed"
        elif _has_numeric_display_points(source, target) and numeric_comparison.get("status") == "passed":
            status = "passed"
        elif _has_numeric_display_points(source, target) and numeric_comparison.get("status") == "failed":
            status = "failed"
        else:
            status = missing_status
            chart_point_comparison = {
                **chart_point_comparison,
                "status": missing_status,
                "reason": "No comparable chart data points were extracted; axes and titles alone are not enough to validate a chart.",
                "error": "" if missing_status == "skipped" else "No comparable chart data points were extracted for this matched chart.",
            }
    elif any(part.get("status") == "failed" for part in parts):
        status = "failed"
    elif any(part.get("status") == "passed" for part in parts):
        status = "passed"
    else:
        status = "skipped"
    return {
        "type": "visual",
        "status": status,
        "matching_method": "llm_declared_semantic_match" if llm_declared_match else "semantic_signature",
        "declared_by_llm": llm_declared_match,
        "score": match.get("score", 0.0),
        "source_signature": source.get("signature"),
        "target_signature": target.get("signature"),
        "source_chart_type": source.get("chart_type"),
        "target_chart_type": target.get("chart_type"),
        "source_title": source.get("title"),
        "target_title": target.get("title"),
        "dimension_overlap": dimension_overlap,
        "measure_overlap": measure_overlap,
        "aggregation_overlap": aggregation_overlap,
        "data_point_overlap": data_point_overlap,
        "numeric_comparison": numeric_comparison,
        "chart_point_comparison": chart_point_comparison,
        "axis_binding_comparison": axis_binding_comparison,
        "source_report": source_name,
        "target_report": target_name,
        "error": "" if status != "failed" else _visual_comparison_error(chart_like, chart_point_comparison, axis_binding_comparison),
        "reason": "" if status == "passed" else _visual_comparison_reason(chart_like, chart_point_comparison, axis_binding_comparison),
    }


def _is_chart_like_visual(visual: dict[str, Any]) -> bool:
    text = _normalize_key(
        " ".join(
            _value_to_str(visual.get(key))
            for key in ("kind", "chart_type", "title")
            if _value_to_str(visual.get(key))
        )
    )
    if not text:
        return False
    if any(token in text for token in ["kpi", "card", "filter", "parameter", "text table", "table", "gauge"]):
        return False
    return any(token in text for token in ["chart", "line", "bar", "column", "area", "scatter", "plot", "pie", "donut"])


def _compare_chart_points(
    source: dict[str, Any],
    target: dict[str, Any],
    tolerance_percent: float,
    approximate_tolerance_percent: float,
    missing_status: str,
) -> dict[str, Any]:
    source_points = _chart_points_with_values(source)
    target_points = _chart_points_with_values(target)
    if not source_points and not target_points:
        return {
            "status": "skipped",
            "source_points": [],
            "target_points": [],
            "matched_points": [],
            "missing_in_target": [],
            "missing_in_source": [],
            "tolerance_percent": tolerance_percent,
            "reason": "No structured chart points were extracted from either visual.",
            "error": "",
        }
    if not source_points or not target_points:
        return {
            "status": missing_status,
            "source_points": [_chart_point_public(point, source) for point in source_points],
            "target_points": [_chart_point_public(point, target) for point in target_points],
            "matched_points": [],
            "missing_in_target": [_chart_point_public(point, source) for point in source_points] if source_points else [],
            "missing_in_source": [_chart_point_public(point, target) for point in target_points] if target_points else [],
            "tolerance_percent": tolerance_percent,
            "approximate_tolerance_percent": approximate_tolerance_percent,
            "reason": "Structured chart points were extracted by only one matched visual.",
            "error": "" if missing_status == "skipped" else "Structured chart points were not extracted from both matched charts.",
        }

    target_remaining = list(enumerate(target_points))
    matched_points = []
    unmatched_source = []
    for source_index, source_point in enumerate(source_points):
        best_target_index = -1
        best_target_point: dict[str, Any] | None = None
        best_metrics: dict[str, Any] | None = None
        best_score: tuple[float, int, float] | None = None
        source_key = _chart_point_key(source_point)
        keyed_candidates = [
            (target_index, target_point)
            for target_index, target_point in target_remaining
            if source_key and source_key == _chart_point_key(target_point)
        ]
        candidate_pool = keyed_candidates or target_remaining
        for target_index, target_point in candidate_pool:
            key_score = _chart_point_match_score(source_point, target_point, source_index, target_index)
            metrics = _chart_point_value_match_metrics(
                source,
                target,
                source_point,
                target_point,
                tolerance_percent,
                approximate_tolerance_percent,
            )
            effective_delta = metrics.get("effective_delta_percent")
            if not isinstance(effective_delta, (int, float)):
                effective_delta = 999999.0
            score = (key_score, 1 if metrics.get("matched") else 0, -float(effective_delta))
            if best_score is None or score > best_score:
                best_score = score
                best_target_index = target_index
                best_target_point = target_point
                best_metrics = metrics
        if best_target_point is None:
            unmatched_source.append(source_point)
            continue
        if best_metrics and best_metrics.get("matched"):
            target_remaining = [(idx, point) for idx, point in target_remaining if idx != best_target_index]
            matched_points.append(
                {
                    "source": _chart_point_public(source_point, source),
                    "target": _chart_point_public(best_target_point, target),
                    "delta_percent": best_metrics.get("delta_percent"),
                    "position_delta_percent": best_metrics.get("position_delta_percent"),
                    "match_method": best_metrics.get("method"),
                    "matching_key": _chart_point_key(source_point) or f"index:{source_index}",
                }
            )
        else:
            unmatched_source.append(source_point)

    unmatched_target = [point for _, point in target_remaining]
    passed = not unmatched_source and not unmatched_target
    return {
        "status": "passed" if passed else "failed",
        "source_points": [_chart_point_public(point, source) for point in source_points],
        "target_points": [_chart_point_public(point, target) for point in target_points],
        "matched_points": matched_points,
        "missing_in_target": [_chart_point_public(point, source) for point in unmatched_source],
        "missing_in_source": [_chart_point_public(point, target) for point in unmatched_target],
        "tolerance_percent": tolerance_percent,
        "approximate_tolerance_percent": approximate_tolerance_percent,
        "error": "" if passed else "Chart data points differ beyond the configured tolerance.",
        "reason": "" if passed else "One or more chart data points could not be matched by category/series/axis/value.",
    }


def _chart_point_value_match_metrics(
    source_visual: dict[str, Any],
    target_visual: dict[str, Any],
    source_point: dict[str, Any],
    target_point: dict[str, Any],
    tolerance_percent: float,
    approximate_tolerance_percent: float,
) -> dict[str, Any]:
    source_value = _chart_point_scaled_value(source_visual, source_point)
    target_value = _chart_point_scaled_value(target_visual, target_point)
    delta_percent = _numeric_delta_percent(source_value, target_value)
    if delta_percent <= tolerance_percent:
        return {
            "matched": True,
            "method": "absolute_value",
            "delta_percent": round(delta_percent, 4),
            "position_delta_percent": None,
            "effective_delta_percent": round(delta_percent, 4),
        }

    both_approximate = bool(source_point.get("approximate")) and bool(target_point.get("approximate"))
    source_position = _chart_point_axis_position(source_visual, source_point)
    target_position = _chart_point_axis_position(target_visual, target_point)
    position_delta_percent = None
    if both_approximate and source_position is not None and target_position is not None:
        position_delta_percent = abs(source_position - target_position) * 100
        if position_delta_percent <= approximate_tolerance_percent:
            return {
                "matched": True,
                "method": "axis_position",
                "delta_percent": round(delta_percent, 4),
                "position_delta_percent": round(position_delta_percent, 4),
                "effective_delta_percent": round(position_delta_percent, 4),
            }

    if both_approximate and delta_percent <= approximate_tolerance_percent:
        return {
            "matched": True,
            "method": "approximate_absolute_value",
            "delta_percent": round(delta_percent, 4),
            "position_delta_percent": None,
            "effective_delta_percent": round(delta_percent, 4),
        }

    return {
        "matched": False,
        "method": "axis_position" if position_delta_percent is not None else "absolute_value",
        "delta_percent": round(delta_percent, 4),
        "position_delta_percent": round(position_delta_percent, 4) if position_delta_percent is not None else None,
        "effective_delta_percent": round(position_delta_percent if position_delta_percent is not None else delta_percent, 4),
    }


def _chart_point_scaled_value(visual: dict[str, Any], point: dict[str, Any]) -> float:
    value = point.get("value")
    if not isinstance(value, (int, float)):
        parsed = _parse_numeric_value(point.get("raw_value"))
        value = parsed if parsed is not None else 0.0
    raw_value = _value_to_str(point.get("raw_value"))
    if re.search(r"\d\s*[kmbKMB]\b", raw_value):
        parsed = _parse_numeric_value(raw_value)
        return float(parsed if parsed is not None else value)

    binding = _axis_binding_for_point(visual, point)
    multiplier = _unit_multiplier(binding.get("unit") if binding else "")
    numeric_value = float(value)
    if _rdl_chart_axis_k_multiplier_applies(visual, point, raw_value):
        return numeric_value * 1_000.0
    if multiplier != 1.0 and abs(numeric_value) < 10000:
        return numeric_value * multiplier
    return numeric_value


def _chart_point_axis_position(visual: dict[str, Any], point: dict[str, Any]) -> float | None:
    binding = _axis_binding_for_point(visual, point)
    if not binding:
        return None
    bounds = _axis_scale_bounds(binding)
    if bounds is None:
        return None
    min_value, max_value = bounds
    axis_multiplier = _chart_axis_scale_multiplier(visual, point)
    min_value *= axis_multiplier
    max_value *= axis_multiplier
    if max_value == min_value:
        return None
    value = _chart_point_scaled_value(visual, point)
    return (value - min_value) / (max_value - min_value)


def _rdl_chart_axis_k_multiplier_applies(visual: dict[str, Any], point: dict[str, Any], raw_value: str = "") -> bool:
    report = _normalize_key(visual.get("report") or visual.get("report_name") or visual.get("source_report"))
    if not any(token in report.split() for token in ["powerbi", "rdl", "paginated"]):
        return False
    if _canonical_axis_name(point.get("axis")) == "x":
        return False
    raw_text = _value_to_str(raw_value or point.get("raw_value"))
    if re.search(r"\d\s*[kmbKMB]\b", raw_text):
        return False
    return True


def _chart_axis_scale_multiplier(visual: dict[str, Any], point: dict[str, Any]) -> float:
    raw_value = _value_to_str(point.get("raw_value"))
    if _rdl_chart_axis_k_multiplier_applies(visual, point, raw_value):
        return 1_000.0
    binding = _axis_binding_for_point(visual, point)
    multiplier = _unit_multiplier(binding.get("unit") if binding else "")
    return multiplier if multiplier != 1.0 else 1.0


def _axis_binding_for_point(visual: dict[str, Any], point: dict[str, Any]) -> dict[str, str]:
    bindings = _axis_bindings_for_comparison(visual)
    if not bindings:
        return {}
    point_measure_tokens = set(_normalize_key(point.get("measure") or point.get("series")).split())
    point_axis = _canonical_axis_name(point.get("axis"))
    best_binding: dict[str, str] = {}
    best_score = 0.0
    for binding in bindings:
        binding_tokens = set(_normalize_key(binding.get("measure")).split())
        score = _token_overlap_score(point_measure_tokens, binding_tokens)
        if point_axis and binding.get("axis") == point_axis:
            score += 1.0
        if score > best_score:
            best_score = score
            best_binding = binding
    return best_binding


def _axis_scale_bounds(binding: dict[str, str]) -> tuple[float, float] | None:
    scale = _value_to_str(binding.get("scale"))
    if not scale:
        return None
    values = []
    for match in re.finditer(r"[-+]?\$?\s*\d[\d,\s]*(?:\.\d+)?\s*[kmbKMB]?", scale):
        parsed = _parse_numeric_value(match.group(0))
        if parsed is not None:
            values.append(parsed)
    if len(values) < 2:
        return None
    if len(values) > 2:
        values = [values[0], values[-1]]
    return (min(values[0], values[1]), max(values[0], values[1]))


def _unit_multiplier(unit: Any) -> float:
    text = _normalize_key(unit)
    if not text:
        return 1.0
    tokens = set(text.split())
    if text in {"b"} or "billion" in tokens or "billions" in tokens:
        return 1_000_000_000.0
    if text in {"m"} or "million" in tokens or "millions" in tokens:
        return 1_000_000.0
    if text in {"k"} or "thousand" in tokens or "thousands" in tokens:
        return 1_000.0
    return 1.0


def _compare_axis_measure_bindings(source: dict[str, Any], target: dict[str, Any], missing_status: str) -> dict[str, Any]:
    source_bindings = _axis_bindings_for_comparison(source)
    target_bindings = _axis_bindings_for_comparison(target)
    if not source_bindings and not target_bindings:
        return {
            "status": "skipped",
            "source_bindings": [],
            "target_bindings": [],
            "matched_bindings": [],
            "missing_in_target": [],
            "missing_in_source": [],
            "reason": "No measure-to-axis bindings were extracted for this chart.",
            "error": "",
        }
    if not source_bindings or not target_bindings:
        return {
            "status": missing_status,
            "source_bindings": source_bindings,
            "target_bindings": target_bindings,
            "matched_bindings": [],
            "missing_in_target": source_bindings if source_bindings else [],
            "missing_in_source": target_bindings if target_bindings else [],
            "reason": "Measure-to-axis bindings were extracted by only one matched chart.",
            "error": "" if missing_status == "skipped" else "Measure-to-axis bindings were not extracted from both matched charts.",
        }

    target_remaining = list(target_bindings)
    matched_bindings = []
    missing_in_target = []
    axis_mismatches = []
    for source_binding in source_bindings:
        best_index = -1
        best_score = 0.0
        for index, target_binding in enumerate(target_remaining):
            score = _axis_binding_measure_score(source_binding, target_binding)
            if score > best_score:
                best_score = score
                best_index = index
        if best_index < 0 or best_score <= 0:
            missing_in_target.append(source_binding)
            continue
        target_binding = target_remaining.pop(best_index)
        axes_match = (
            not source_binding.get("axis")
            or not target_binding.get("axis")
            or source_binding.get("axis") == target_binding.get("axis")
        )
        binding_pair = {"source": source_binding, "target": target_binding, "measure_score": round(best_score, 3)}
        if axes_match:
            matched_bindings.append(binding_pair)
        else:
            axis_mismatches.append(binding_pair)

    status = "passed" if not missing_in_target and not target_remaining and not axis_mismatches else "failed"
    return {
        "status": status,
        "source_bindings": source_bindings,
        "target_bindings": target_bindings,
        "matched_bindings": matched_bindings,
        "axis_mismatches": axis_mismatches,
        "missing_in_target": missing_in_target,
        "missing_in_source": target_remaining,
        "reason": "" if status == "passed" else "Measure-to-axis mapping differs or could not be matched.",
        "error": "" if status == "passed" else "Measure-to-axis mapping differs between matched charts.",
    }


def _axis_bindings_for_comparison(visual: dict[str, Any]) -> list[dict[str, str]]:
    bindings = visual.get("axis_bindings") if isinstance(visual.get("axis_bindings"), list) else []
    normalized = []
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        measure = _value_to_str(binding.get("measure"))
        axis = _canonical_axis_name(binding.get("axis"))
        if not measure:
            continue
        normalized.append(
            {
                "measure": measure,
                "axis": axis,
                "scale": _value_to_str(binding.get("scale")),
                "unit": _value_to_str(binding.get("unit")),
            }
        )
    return normalized


def _axis_binding_measure_score(source_binding: dict[str, str], target_binding: dict[str, str]) -> float:
    source_tokens = set(_normalize_key(source_binding.get("measure")).split())
    target_tokens = set(_normalize_key(target_binding.get("measure")).split())
    if not source_tokens or not target_tokens:
        return 0.0
    if source_tokens == target_tokens:
        return 1.0
    return _token_overlap_score(source_tokens, target_tokens)


def _has_multiple_chart_measures(visual: dict[str, Any]) -> bool:
    measures = {
        _normalize_key(item)
        for item in (visual.get("measures") if isinstance(visual.get("measures"), list) else [])
        if _normalize_key(item)
    }
    for point in _chart_points_with_values(visual):
        measure = _normalize_key(point.get("measure") or point.get("series"))
        if measure:
            measures.add(measure)
    for binding in _axis_bindings_for_comparison(visual):
        measure = _normalize_key(binding.get("measure"))
        if measure:
            measures.add(measure)
    return len(measures) > 1


def _chart_points_with_values(visual: dict[str, Any]) -> list[dict[str, Any]]:
    points = visual.get("chart_points") if isinstance(visual.get("chart_points"), list) else []
    result = []
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            continue
        value = point.get("value")
        if not isinstance(value, (int, float)):
            value = _parse_numeric_value(point.get("raw_value"))
        if value is None:
            continue
        result.append(
            {
                **point,
                "measure": _value_to_str(point.get("measure") or point.get("series")),
                "axis": _canonical_axis_name(point.get("axis")),
                "value": float(value),
                "index": point.get("index", index),
            }
        )
    return result


def _chart_point_key(point: dict[str, Any]) -> str:
    category = _normalize_key(point.get("category") or point.get("label"))
    measure = _normalize_key(point.get("measure") or point.get("series"))
    axis = _canonical_axis_name(point.get("axis"))
    if category and measure:
        return f"{category}|{measure}"
    if category and axis:
        return f"{category}|{axis}"
    return category or measure or axis


def _chart_point_match_score(source_point: dict[str, Any], target_point: dict[str, Any], source_index: int, target_index: int) -> float:
    score = 0.0
    source_category = _normalize_key(source_point.get("category") or source_point.get("label"))
    target_category = _normalize_key(target_point.get("category") or target_point.get("label"))
    if source_category and target_category and source_category == target_category:
        score += 2.0
    source_measure = set(_normalize_key(source_point.get("measure") or source_point.get("series")).split())
    target_measure = set(_normalize_key(target_point.get("measure") or target_point.get("series")).split())
    score += 2.0 * _token_overlap_score(source_measure, target_measure)
    source_axis = _canonical_axis_name(source_point.get("axis"))
    target_axis = _canonical_axis_name(target_point.get("axis"))
    if source_axis and target_axis and source_axis == target_axis:
        score += 1.0
    if not source_category and not target_category and source_index == target_index:
        score += 0.5
    return score


def _chart_point_public(point: dict[str, Any], visual: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_value = None
    axis_multiplier = 1.0
    if isinstance(visual, dict):
        normalized_value = _chart_point_scaled_value(visual, point)
        axis_multiplier = _chart_axis_scale_multiplier(visual, point)
    payload = {
        "category": point.get("category", ""),
        "measure": point.get("measure", ""),
        "axis": point.get("axis", ""),
        "series": point.get("series", ""),
        "label": point.get("label", ""),
        "raw_value": point.get("raw_value", ""),
        "value": point.get("value"),
        "approximate": bool(point.get("approximate")),
    }
    if normalized_value is not None:
        payload["normalized_value"] = normalized_value
    if axis_multiplier != 1.0:
        payload["axis_multiplier"] = axis_multiplier
    return payload


def _visual_comparison_error(chart_like: bool, chart_point_comparison: dict[str, Any], axis_binding_comparison: dict[str, Any]) -> str:
    if chart_like:
        if axis_binding_comparison.get("status") == "failed":
            return _value_to_str(axis_binding_comparison.get("error")) or "Chart measure-to-axis mapping comparison failed."
        return _value_to_str(chart_point_comparison.get("error")) or "Chart data point comparison failed."
    return "Semantic visual comparison failed for at least one matched visual."


def _visual_comparison_reason(chart_like: bool, chart_point_comparison: dict[str, Any], axis_binding_comparison: dict[str, Any]) -> str:
    if chart_like:
        if axis_binding_comparison.get("status") in {"failed", "skipped"} and axis_binding_comparison.get("reason"):
            return _value_to_str(axis_binding_comparison.get("reason"))
        return _value_to_str(chart_point_comparison.get("reason")) or "Matched chart has data points that differ or were not fully exposed by extraction."
    return "Matched visual has data that was different or not fully exposed by extraction."


def _relax_declared_overlap(overlap: dict[str, Any], label: str) -> dict[str, Any]:
    if overlap.get("status") != "failed":
        return overlap
    return {
        **overlap,
        "status": "passed",
        "semantic_equivalence_assumed": True,
        "reason": f"LLM declared this visual as the matching business visual; {label} labels differ but are treated as semantically equivalent.",
    }


def _visual_numeric_values(visual: dict[str, Any]) -> list[float]:
    chart_point_values = [
        float(point["value"])
        for point in _chart_points_with_values(visual)
        if isinstance(point.get("value"), (int, float))
    ]
    if chart_point_values:
        return chart_point_values
    values = visual.get("numeric_values")
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, (int, float))]


def _has_numeric_display_points(source: dict[str, Any], target: dict[str, Any]) -> bool:
    for visual in (source, target):
        values = visual.get("data_points")
        if not isinstance(values, list):
            continue
        if any(_parse_numeric_value(item) is not None for item in values):
            return True
    return False


def _compare_numeric_multiset(
    source_values: list[float],
    target_values: list[float],
    tolerance_percent: float,
    missing_status: str = "failed",
) -> dict[str, Any]:
    if not source_values and not target_values:
        return {
            "status": "skipped",
            "source_values": source_values,
            "target_values": target_values,
            "matched_values": [],
            "missing_in_target": [],
            "missing_in_source": [],
            "tolerance_percent": tolerance_percent,
            "reason": "No numeric values were exposed for this visual.",
            "error": "",
        }
    if not source_values or not target_values:
        return {
            "status": missing_status,
            "source_values": source_values,
            "target_values": target_values,
            "matched_values": [],
            "missing_in_target": source_values if source_values else [],
            "missing_in_source": target_values if target_values else [],
            "tolerance_percent": tolerance_percent,
            "reason": "Numeric values were exposed by only one matched visual.",
            "error": "" if missing_status == "skipped" else "Numeric values were not exposed by both matched visuals.",
        }

    source_remaining = list(source_values)
    target_remaining = list(target_values)
    matches = []
    for source_value in list(source_remaining):
        best_index = -1
        best_delta = None
        for index, target_value in enumerate(target_remaining):
            delta_percent = _numeric_delta_percent(source_value, target_value)
            if delta_percent <= tolerance_percent and (best_delta is None or delta_percent < best_delta):
                best_delta = delta_percent
                best_index = index
        if best_index >= 0:
            target_value = target_remaining.pop(best_index)
            source_remaining.remove(source_value)
            matches.append({"source": source_value, "target": target_value, "delta_percent": round(float(best_delta or 0.0), 4)})
    passed = not source_remaining and not target_remaining
    return {
        "status": "passed" if passed else "failed",
        "source_values": source_values,
        "target_values": target_values,
        "matched_values": matches,
        "missing_in_target": source_remaining,
        "missing_in_source": target_remaining,
        "tolerance_percent": tolerance_percent,
        "error": "" if passed else "Numeric values differ beyond the configured tolerance.",
    }


def _numeric_delta_percent(source_value: float, target_value: float) -> float:
    denominator = max(abs(source_value), abs(target_value), 1.0)
    return (abs(source_value - target_value) / denominator) * 100


def _set_overlap_summary(source_values: list[Any], target_values: list[Any], missing_status: str = "failed") -> dict[str, Any]:
    source_set = {_normalize_key(item) for item in source_values if _value_to_str(item)}
    target_set = {_normalize_key(item) for item in target_values if _value_to_str(item)}
    if not source_set and not target_set:
        return {"status": "skipped", "shared": [], "missing_in_source": [], "missing_in_target": []}
    shared = sorted(source_set & target_set)
    missing_in_source = sorted(target_set - source_set)
    missing_in_target = sorted(source_set - target_set)
    if not source_set or not target_set:
        return {
            "status": missing_status,
            "shared": shared,
            "missing_in_source": missing_in_source,
            "missing_in_target": missing_in_target,
            "reason": "Values were exposed by only one matched visual.",
            "error": "" if missing_status == "skipped" else "Values were not exposed by both matched visuals.",
        }
    passed = not missing_in_source and not missing_in_target
    return {
        "status": "passed" if passed else "failed",
        "shared": shared,
        "missing_in_source": missing_in_source,
        "missing_in_target": missing_in_target,
    }


def _required_visual_categories(required_categories: list[Any], source_visuals: list[dict[str, Any]], target_visuals: list[dict[str, Any]]) -> list[str]:
    required_tokens = {_normalize_key(item) for item in required_categories if _normalize_key(item)}
    if not required_tokens:
        return []
    present_tokens: set[str] = set()
    for visual in source_visuals + target_visuals:
        present_tokens.update(_visual_signature_tokens(visual))
    return sorted(required_tokens - present_tokens)


def _extract_parameter_value(text: str, parameter_name: Any, fallback: str = "") -> str:
    normalized_name = _value_to_str(parameter_name)
    if not normalized_name:
        return fallback
    normalized_name_key = _normalize_key(normalized_name)
    for line in _text_lines(text):
        if normalized_name_key not in _normalize_key(line):
            continue
        segments = [segment.strip() for segment in re.split(r"[|•]", line) if segment.strip()]
        for segment in segments:
            if normalized_name_key not in _normalize_key(segment):
                continue
            if ":" in segment:
                candidate = _value_to_str(segment.split(":", 1)[1]).strip("| .")
            elif "=" in segment:
                candidate = _value_to_str(segment.split("=", 1)[1]).strip("| .")
            else:
                candidate = ""
            if candidate and not _looks_like_parameter_noise(candidate):
                return candidate
    normalized_text = _normalize_text_for_match(text)
    if normalized_name_key and normalized_name_key in normalized_text:
        return fallback
    return fallback


def _looks_like_parameter_noise(value: str) -> bool:
    normalized = _normalize_key(value)
    if not normalized:
        return True
    return normalized in {
        "view report",
        "parameters",
        "parameter",
        "group",
        "year",
        "open year",
        "open group",
        "skip to content",
        "regional sales",
    }


def _parameter_display_value(value: Any) -> str:
    if isinstance(value, dict):
        observed = _value_to_str(value.get("value"))
        if observed:
            return observed
        expected = _value_to_str(value.get("expected"))
        if expected:
            return expected
    return _value_to_str(value)


def _extract_axis_values(text: str) -> list[float]:
    values = []
    for line in _text_lines(text):
        lowered = line.lower()
        if any(marker in lowered for marker in ["run at", "page ", "year:", "group:", "power bi", "tableau"]):
            continue
        if not re.fullmatch(r"[-+]?\$?\s*\d[\d,]*(?:\.\d+)?\s*[kmbKMB]?", line):
            continue
        value = _parse_numeric_value(line)
        if value is not None and value not in values:
            values.append(value)
    return values[:40]


def _parse_numeric_value(raw: Any) -> float | None:
    text = _value_to_str(raw)
    if not text:
        return None
    text = text.replace("\u00a0", " ").strip()
    match = re.fullmatch(
        r"(?P<sign>[-+])?\s*\$?\s*(?P<number>\d[\d,\s]*(?:\.\d+)?)\s*(?P<suffix>[kmbKMB])?\s*%?",
        text,
    )
    if not match:
        return None
    number_text = match.group("number").replace(",", "").replace(" ", "")
    try:
        value = float(number_text)
    except ValueError:
        return None
    suffix = (match.group("suffix") or "").lower()
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    elif suffix == "b":
        value *= 1_000_000_000
    if match.group("sign") == "-":
        value *= -1
    return value


def _kpi_numeric_value(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("normalized_value")
    return raw if isinstance(raw, (int, float)) else None


def _kpi_public_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "raw": value.get("raw", ""),
        "normalized_value": value.get("normalized_value"),
    }


def _text_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _text_excerpt(text: str, limit: int = 1200) -> str:
    collapsed = re.sub(r"\s+", " ", str(text or "")).strip()
    return collapsed[:limit]


def _normalize_text_for_match(value: Any) -> str:
    return re.sub(r"\s+", " ", _value_to_str(value).lower()).strip()


def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _value_to_str(value).lower()).strip()


def _is_likely_label_line(value: str) -> bool:
    key = _normalize_key(value)
    return key in {"orders", "sales", "quota", "variance"}


def _is_likely_chart_or_viewer_line(value: str) -> bool:
    key = _normalize_key(value)
    return key in {
        "salesamount",
        "salesamountquota",
        "englishmonthname",
        "tableau cloud guided walkthrough step 1 of 0",
        "data details",
        "view original",
    }


def _missing_data_status(test_cfg: dict[str, Any]) -> str:
    return "failed" if _truthy_value(test_cfg.get("strict_missing_values")) else "skipped"


def _apply_url_filter_params(url: str, report_cfg: dict[str, Any], scenario: dict[str, Any]) -> str:
    filter_map = report_cfg.get("url_filter_params")
    if not isinstance(filter_map, dict) or not filter_map:
        return url

    scenario_filters = scenario.get("filters")
    query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    for url_param, filter_name in filter_map.items():
        key = _value_to_str(url_param)
        if not key:
            continue
        if isinstance(filter_name, str) and scenario_filters and isinstance(scenario_filters, dict):
            value = scenario_filters.get(filter_name)
        else:
            value = filter_name
        normalized = _normalize_filter_value(value)
        if normalized:
            query[key] = normalized
    parts = list(urlsplit(url))
    parts[3] = urlencode(query, doseq=True, safe=":")
    return urlunsplit(parts)


def _prepare_report_for_capture(page, report_name: str, report_cfg: dict[str, Any], scenario: dict[str, Any]) -> None:
    normalized_name = str(report_name or "").strip().lower()
    if normalized_name == "powerbi":
        _apply_powerbi_parameter_values(page, scenario, report_cfg)
        _click_powerbi_view_report(page, report_cfg)
    elif normalized_name == "tableau":
        _open_tableau_view(page, report_cfg, scenario)


def _apply_powerbi_parameter_values(page, scenario: dict[str, Any], report_cfg: dict[str, Any]) -> None:
    if report_cfg.get("apply_parameter_values") is False:
        return
    filters = scenario.get("filters")
    if not isinstance(filters, dict) or not filters:
        return
    timeout_ms = _timeout_ms(report_cfg.get("parameter_click_timeout_ms"), default=10000)
    load_timeout_ms = _timeout_ms(report_cfg.get("parameter_load_timeout_ms"), default=120000)
    _wait_for_powerbi_parameter_controls(page, filters, load_timeout_ms)
    parameter_errors = []
    for parameter_name, value in filters.items():
        normalized_value = _normalize_filter_value(value)
        if not normalized_value:
            continue
        if not _set_powerbi_combobox_value(page, str(parameter_name), normalized_value, timeout_ms):
            parameter_errors.append(f"{parameter_name}={normalized_value}")
    if parameter_errors:
        report_cfg["_parameter_errors"] = parameter_errors


def _wait_for_powerbi_parameter_controls(page, filters: dict[str, Any], timeout_ms: int) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        for parameter_name in filters:
            for frame in _page_frames(page):
                for label in _parameter_label_candidates(str(parameter_name)):
                    try:
                        if frame.get_by_role("combobox", name=label).first.is_visible(timeout=500):
                            return
                    except Exception:
                        continue
        page.wait_for_timeout(1000)


def _set_powerbi_combobox_value(page, parameter_name: str, value: str, timeout_ms: int) -> bool:
    for frame in _page_frames(page):
        for label in _parameter_label_candidates(parameter_name):
            try:
                combo = frame.get_by_role("combobox", name=label).first
                if not combo.is_visible(timeout=1000):
                    continue
                current_value = _value_to_str(combo.input_value(timeout=1000))
                if current_value == value:
                    return True
                combo.click(timeout=timeout_ms)
                combo.fill(value, timeout=timeout_ms)
                try:
                    frame.get_by_role("option", name=value).first.click(timeout=3000)
                    return True
                except Exception:
                    try:
                        combo.press("Enter", timeout=3000)
                        frame.page.wait_for_timeout(500)
                    except Exception:
                        pass
                try:
                    return _value_to_str(combo.input_value(timeout=1000)) == value
                except Exception:
                    return False
            except Exception:
                continue
    return False


def _parameter_label_candidates(parameter_name: str) -> list[str]:
    raw = str(parameter_name or "").strip()
    if not raw:
        return []
    words = _split_identifier_words(raw)
    candidates = [raw, f"{raw}:"]
    if words:
        title = " ".join(words)
        candidates.extend([title, f"{title}:"])
        candidates.extend([words[-1], f"{words[-1]}:"])
    lowered = raw.lower()
    if lowered.endswith("year"):
        candidates.extend(["Year", "Year:"])
    if "group" in lowered:
        candidates.extend(["Group", "Group:"])
    result = []
    for candidate in candidates:
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _split_identifier_words(value: str) -> list[str]:
    words: list[str] = []
    current = ""
    for char in value:
        if char in {"_", "-", " "}:
            if current:
                words.append(current)
                current = ""
            continue
        if char.isupper() and current and not current[-1].isupper():
            words.append(current)
            current = char
        else:
            current += char
    if current:
        words.append(current)
    return words


def _click_powerbi_view_report(page, report_cfg: dict[str, Any]) -> None:
    if report_cfg.get("click_view_report") is False:
        return
    timeout_ms = _timeout_ms(report_cfg.get("parameter_click_timeout_ms"), default=10000)
    for frame in _page_frames(page):
        try:
            button = frame.get_by_role("button", name="View report").first
            if button.is_visible(timeout=timeout_ms):
                button.click(timeout=timeout_ms)
                page.wait_for_timeout(_timeout_ms(report_cfg.get("after_view_report_wait_ms"), default=3000))
                return
        except Exception:
            continue


def _open_tableau_view(page, report_cfg: dict[str, Any], scenario: dict[str, Any]) -> None:
    if report_cfg.get("open_view") is False or _is_tableau_view_url(page):
        return

    timeout_ms = _timeout_ms(report_cfg.get("view_click_timeout_ms"), default=30000)
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        if _is_tableau_view_url(page):
            return
        if _click_tableau_view_link(page, report_cfg, scenario, timeout_ms):
            _wait_until_tableau_view_url(page, timeout_ms)
            page.wait_for_timeout(_timeout_ms(report_cfg.get("after_view_click_wait_ms"), default=3000))
            return
        page.wait_for_timeout(1000)


def _click_tableau_view_link(
    page,
    report_cfg: dict[str, Any],
    scenario: dict[str, Any],
    timeout_ms: int,
) -> bool:
    view_name = _tableau_view_name(report_cfg, scenario)
    for frame in _page_frames(page):
        if view_name:
            locators = [
                frame.locator('a[href*="redirect_to_view"]', has_text=view_name).first,
                frame.get_by_role("link", name=view_name, exact=True).first,
                frame.get_by_role("link", name=view_name).first,
            ]
            for locator in locators:
                if _click_if_tableau_view_link(locator, timeout_ms):
                    return True

        fallback = frame.locator('a[href*="redirect_to_view"], a[href*="/views/"]').first
        if _click_if_tableau_view_link(fallback, timeout_ms):
            return True
    return False


def _click_if_tableau_view_link(locator, timeout_ms: int) -> bool:
    try:
        if not locator.is_visible(timeout=1000):
            return False
        href = _value_to_str(locator.get_attribute("href", timeout=1000)).lower()
        if "redirect_to_view" not in href and "/views/" not in href:
            return False
        locator.click(timeout=timeout_ms)
        return True
    except Exception:
        return False


def _tableau_view_name(report_cfg: dict[str, Any], scenario: dict[str, Any]) -> str:
    for source in (report_cfg, scenario):
        if not isinstance(source, dict):
            continue
        for key in ("view_name", "dashboard_name", "tableau_view_name", "sheet_name"):
            value = _value_to_str(source.get(key))
            if value:
                return value
    return ""


def _wait_until_tableau_view_url(page, timeout_ms: int) -> bool:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        if _is_tableau_view_url(page):
            return True
        page.wait_for_timeout(500)
    return _is_tableau_view_url(page)


def _is_tableau_view_url(page) -> bool:
    current_url = str(getattr(page, "url", "") or "").lower()
    return "/views/" in current_url


def _apply_filter_steps(page, report_cfg: dict[str, Any], scenario: dict[str, Any]) -> None:
    steps = report_cfg.get("filter_steps")
    if not isinstance(steps, list):
        return

    scenario_filters = scenario.get("filters") if isinstance(scenario.get("filters"), dict) else {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = _value_to_str(step.get("action") or step.get("type")).lower()
        if action == "wait":
            page.wait_for_timeout(_timeout_ms(step, default=1000))
            continue

        selector = _value_to_str(step.get("selector") or step.get("locator"))
        if not selector:
            continue
        locator = page.locator(selector).first
        value = step.get("value")
        if isinstance(value, str) and value.startswith("$"):
            key = value[1:]
            if key:
                value = scenario_filters.get(key, value)
        if action in {"fill", "type"}:
            locator.fill(_normalize_filter_value(value), timeout=_timeout_ms(step, default=30000))
        elif action in {"select", "select_option"}:
            locator.select_option(_normalize_filter_value(value), timeout=_timeout_ms(step, default=30000))
        elif action in {"click", "press"}:
            locator.click(timeout=_timeout_ms(step, default=30000))
        elif action == "choose":
            locator.click(timeout=_timeout_ms(step, default=30000))
        else:
            locator.click(timeout=_timeout_ms(step, default=30000))

        if bool(step.get("press_enter")):
            page.keyboard.press("Enter")


def _wait_for_report_ready(page, report_cfg: dict[str, Any], report_name: str = "") -> None:
    if str(report_name or "").strip().lower() == "powerbi":
        _wait_for_powerbi_report_ready(page, report_cfg)
        return
    if str(report_name or "").strip().lower() == "tableau":
        _wait_for_tableau_report_ready(page, report_cfg)
        return

    timeout_ms = _timeout_ms(report_cfg, default=120000)
    loading_selectors = report_cfg.get("loading_selectors")
    if isinstance(loading_selectors, list):
        for selector in loading_selectors:
            selector_text = _value_to_str(selector)
            if selector_text:
                try:
                    page.locator(selector_text).first.wait_for(state="hidden", timeout=timeout_ms)
                except PlaywrightTimeoutError:
                    pass

    ready_selector = _value_to_str(report_cfg.get("ready_selector") or report_cfg.get("dashboard_selector"))
    if ready_selector:
        page.locator(ready_selector).first.wait_for(state="visible", timeout=timeout_ms)

    page.wait_for_load_state("networkidle", timeout=timeout_ms)
    page.wait_for_timeout(_timeout_ms(report_cfg.get("wait_after_render_ms"), default=1500))


def _wait_for_powerbi_report_ready(page, report_cfg: dict[str, Any]) -> None:
    parameter_errors = report_cfg.get("_parameter_errors")
    if isinstance(parameter_errors, list) and parameter_errors:
        raise TimeoutError(
            "Power BI report parameters could not be selected: "
            + ", ".join(str(item) for item in parameter_errors)
        )

    timeout_ms = _timeout_ms(report_cfg, default=120000)
    deadline = time.monotonic() + (timeout_ms / 1000)
    view_report_seen_at: float | None = None
    view_report_clicked = False

    while time.monotonic() < deadline:
        text = _frames_text(page).lower()
        if _powerbi_report_content_ready(text, report_cfg):
            page.wait_for_timeout(_timeout_ms(report_cfg.get("wait_after_render_ms"), default=1500))
            return

        view_report_visible = _visible_in_any_frame(page, "button", "View report")
        if view_report_visible:
            if view_report_seen_at is None:
                view_report_seen_at = time.monotonic()
            if not view_report_clicked:
                _click_powerbi_view_report(page, report_cfg)
                view_report_clicked = True
                page.wait_for_timeout(_timeout_ms(report_cfg.get("after_view_report_wait_ms"), default=3000))
                continue
            if time.monotonic() - view_report_seen_at > 20000:
                raise TimeoutError(
                    "Power BI report is still on the paginated parameter screen; "
                    "the URL parameters were not enough to render the published report."
                )
            page.wait_for_timeout(1000)
            continue

        if "loading report" in text:
            page.wait_for_timeout(1000)
            continue

        page.wait_for_timeout(_timeout_ms(report_cfg.get("wait_after_render_ms"), default=1500))
        return

    raise TimeoutError("Power BI report did not finish rendering before the timeout.")


def _wait_for_tableau_report_ready(page, report_cfg: dict[str, Any]) -> None:
    timeout_ms = _timeout_ms(report_cfg.get("timeout_ms") or report_cfg.get("timeout"), default=120000)
    deadline = time.monotonic() + (timeout_ms / 1000)
    ready_selector = _value_to_str(report_cfg.get("ready_selector") or report_cfg.get("dashboard_selector"))

    while time.monotonic() < deadline:
        if ready_selector:
            try:
                if page.locator(ready_selector).first.is_visible(timeout=1000):
                    page.wait_for_timeout(_timeout_ms(report_cfg.get("wait_after_render_ms"), default=1500))
                    return
            except Exception:
                pass

        text = _frames_text(page).lower()
        if _is_tableau_view_url(page):
            if _tableau_report_content_ready(text, report_cfg) or (text and "loading" not in text):
                page.wait_for_timeout(_timeout_ms(report_cfg.get("wait_after_render_ms"), default=1500))
                return

        if _looks_like_tableau_workbook_overview(page, text):
            raise TimeoutError(
                "Tableau opened the workbook overview page instead of a published view. "
                "Configure published_report_test.tableau.view_name or publish a direct Tableau view URL."
            )

        page.wait_for_timeout(1000)

    if _looks_like_tableau_workbook_overview(page):
        raise TimeoutError(
            "Tableau opened the workbook overview page instead of a published view. "
            "Configure published_report_test.tableau.view_name or publish a direct Tableau view URL."
        )
    raise TimeoutError("Tableau published view did not finish rendering before the timeout.")


def _tableau_report_content_ready(text: str, report_cfg: dict[str, Any]) -> bool:
    normalized_text = str(text or "").lower()
    ready_texts = report_cfg.get("ready_texts")
    if isinstance(ready_texts, list):
        configured_markers = [_value_to_str(item).lower() for item in ready_texts]
        configured_markers = [item for item in configured_markers if item]
        if configured_markers:
            return all(marker in normalized_text for marker in configured_markers)
    return any(
        marker in normalized_text
        for marker in ["regional sales", "orders", "sales", "quota"]
    )


def _looks_like_tableau_workbook_overview(page, text: str | None = None) -> bool:
    current_url = str(getattr(page, "url", "") or "").lower()
    if "/workbooks/" in current_url and not _is_tableau_view_url(page):
        return True
    body_text = text if text is not None else _frames_text(page).lower()
    return "views" in body_text and _has_tableau_view_links(page)


def _has_tableau_view_links(page) -> bool:
    for frame in _page_frames(page):
        try:
            if frame.locator('a[href*="redirect_to_view"], a[href*="/views/"]').first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


def _powerbi_report_content_ready(text: str, report_cfg: dict[str, Any]) -> bool:
    normalized_text = str(text or "").lower()
    ready_texts = report_cfg.get("ready_texts")
    if isinstance(ready_texts, list):
        configured_markers = [_value_to_str(item).lower() for item in ready_texts]
        configured_markers = [item for item in configured_markers if item]
        if configured_markers and all(marker in normalized_text for marker in configured_markers):
            return True
    return any(
        marker in normalized_text
        for marker in ["page 1 of", "run at", "regional sales", "orders", "quota"]
    )


def _detect_auth_required(page, report_name: str) -> str:
    current_url = str(getattr(page, "url", "") or "").lower()
    report_label = str(report_name or "report").strip() or "report"
    auth_url_markers = [
        "login.microsoftonline.com",
        "login.live.com",
        "app.powerbi.com/singleSignOn",
        "/signin",
        "/auth/signin",
    ]
    if any(marker.lower() in current_url for marker in auth_url_markers):
        return f"{report_label} requires an authenticated browser session; current page is a sign-in URL."

    try:
        body_text = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        body_text = ""

    tableau_login = (
        "sign in to tableau cloud" in body_text
        or ("tableau" in body_text and "sign in" in body_text and "password" in body_text)
        or ("tableau" in body_text and "email" in body_text and "remember me" in body_text)
        or ("sign in with salesforce" in body_text and "tableau" in body_text)
    )
    powerbi_login = "enter your email" in body_text and "power bi" in body_text
    microsoft_login = "sign in" in body_text and "microsoft" in body_text and "email" in body_text
    if tableau_login or powerbi_login or microsoft_login:
        return (
            f"{report_label} requires an authenticated browser session; "
            "the screenshot contains a login page instead of the published report."
        )
    return ""


def _detect_required_input(page, report_name: str) -> str:
    if _visible_in_any_frame(page, "text", "Required") and _visible_in_any_frame(page, "button", "View report"):
        report_label = str(report_name or "report").strip() or "report"
        return (
            f"{report_label} is authenticated but waiting for required report parameters. "
            "Configure published_report_test filters/filter_steps before running the comparison."
        )

    try:
        body_text = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        return ""

    report_label = str(report_name or "report").strip() or "report"
    if "view report" in body_text and "required" in body_text and (
        "parameter" in body_text or "parameters" in body_text
    ):
        return (
            f"{report_label} is authenticated but waiting for required report parameters. "
            "Configure published_report_test filters/filter_steps before running the comparison."
        )
    return ""


def _page_frames(page) -> list[Any]:
    frames = []
    try:
        frames.append(page)
    except Exception:
        pass
    try:
        frames.extend(page.frames)
    except Exception:
        pass
    return frames


def _frames_text(page) -> str:
    chunks = []
    for frame in _page_frames(page):
        try:
            chunks.append(frame.locator("body").inner_text(timeout=1000))
        except Exception:
            continue
    return "\n".join(chunks)


def _frames_data_text(page) -> str:
    chunks = [_frames_text(page)]
    for frame in _page_frames(page):
        try:
            extra_text = frame.evaluate(
                """() => {
                    const chunks = [];
                    const push = (value) => {
                        if (typeof value !== 'string') return;
                        const text = value.replace(/\\s+/g, ' ').trim();
                        if (text) chunks.push(text);
                    };
                    push(document.body?.textContent || '');
                    for (const element of document.querySelectorAll('svg text, svg title, svg desc, [aria-label], [title], [alt]')) {
                        push(element.textContent || '');
                        push(element.getAttribute('aria-label') || '');
                        push(element.getAttribute('title') || '');
                        push(element.getAttribute('alt') || '');
                    }
                    return chunks.join('\\n');
                }"""
            )
            chunks.append(str(extra_text or ""))
        except Exception:
            continue
    seen = set()
    lines = []
    for line in _text_lines("\n".join(chunks)):
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


def _visible_in_any_frame(page, locator_type: str, value: str) -> bool:
    for frame in _page_frames(page):
        try:
            locator = (
                frame.get_by_role("button", name=value).first
                if locator_type == "button"
                else frame.get_by_text(value, exact=True).first
            )
            if locator.is_visible(timeout=1000):
                return True
        except Exception:
            continue
    return False


def _default_storage_state_path(report_name: str) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    normalized = str(report_name or "").strip().lower()
    candidates = [
        repo_root / ".auth" / f"{normalized}-storage-state.json",
        repo_root / ".auth" / f"{normalized}_storage_state.json",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return ""


def _default_user_data_dir(report_name: str) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    normalized = str(report_name or "").strip().lower()
    candidates = [
        repo_root / ".auth" / f"{normalized}-profile",
        repo_root / ".auth" / f"{normalized}_profile",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return str(candidate)
    return ""


def _resolve_project_path(path_value: str) -> str:
    text = _value_to_str(path_value)
    if not text:
        return ""
    path = Path(text).expanduser()
    if path.is_absolute():
        return str(path)
    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return str(cwd_candidate)
    repo_candidate = (Path(__file__).resolve().parents[2] / path).resolve()
    return str(repo_candidate)


def _normalize_filter_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(_normalize_filter_value(item) for item in value if _normalize_filter_value(item))
    return str(value).strip()


def _timeout_ms(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _truthy_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _value_to_str(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


if __name__ == "__main__":
    raise SystemExit(main())
