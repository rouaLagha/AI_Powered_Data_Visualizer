from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
POWERBI_API_ROOT = "https://api.powerbi.com/v1.0/myorg"


def publish_rdl_if_configured(
    rdl_path: str | Path,
    powerbi_config: dict | None,
    timestamp_utc: str,
    report_name: str | None = None,
) -> dict[str, Any]:
    path = Path(rdl_path)
    if not isinstance(powerbi_config, dict) or not _truthy(powerbi_config.get("enabled", False)):
        return {
            "status": "skipped",
            "reason": "powerbi_service publishing is disabled",
            "rdl_path": str(path),
            "timestamp_utc": timestamp_utc,
        }

    if not path.exists():
        raise FileNotFoundError(f"RDL file not found for Power BI publish: {path}")

    if path.suffix.lower() != ".rdl":
        raise ValueError(f"Power BI paginated report publish expects a .rdl file: {path}")

    token = _resolve_access_token(powerbi_config)
    workspace_id = _optional_str(
        powerbi_config.get("workspace_id")
        or powerbi_config.get("group_id")
        or powerbi_config.get("workspaceId")
        or powerbi_config.get("groupId")
    )
    conflict = _optional_str(powerbi_config.get("name_conflict"), default="Abort")
    timeout_seconds = _safe_timeout(powerbi_config.get("timeout_seconds"), default=300)
    display_name = _timestamped_rdl_name(
        base_name=_optional_str(powerbi_config.get("report_name"), default=report_name or path.stem),
        timestamp_utc=timestamp_utc,
    )

    endpoint = f"{POWERBI_API_ROOT}/imports"
    if workspace_id:
        endpoint = f"{POWERBI_API_ROOT}/groups/{workspace_id}/imports"
    endpoint = f"{endpoint}?{urlencode({'datasetDisplayName': display_name, 'nameConflict': conflict})}"

    boundary = f"----RdlToTwbPowerBI{timestamp_utc}"
    file_bytes = path.read_bytes()
    body = _multipart_body(
        boundary=boundary,
        field_name="file",
        file_name=display_name,
        file_bytes=file_bytes,
        content_type=mimetypes.guess_type(display_name)[0] or "application/octet-stream",
    )

    request = Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            status_code = int(getattr(response, "status", 0) or response.getcode())
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {
            "status": "failed",
            "http_status": int(getattr(exc, "code", 0) or 0),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "response": _json_or_text(error_body),
            "rdl_path": str(path),
            "display_name": display_name,
            "workspace_id": workspace_id or "",
            "timestamp_utc": timestamp_utc,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "rdl_path": str(path),
            "display_name": display_name,
            "workspace_id": workspace_id or "",
            "timestamp_utc": timestamp_utc,
        }

    payload = _json_or_text(response_body)
    return {
        "status": "published" if 200 <= status_code < 300 else "failed",
        "http_status": status_code,
        "workspace_id": workspace_id or "",
        "display_name": display_name,
        "rdl_path": str(path),
        "timestamp_utc": timestamp_utc,
        "response": payload,
    }


def _resolve_access_token(cfg: dict[str, Any]) -> str:
    access_token = _resolve_secret(
        cfg.get("access_token") or cfg.get("bearer_token"),
        fallback_env="POWERBI_ACCESS_TOKEN",
    )
    if access_token:
        return access_token

    tenant_id = _resolve_secret(cfg.get("tenant_id"), fallback_env="POWERBI_TENANT_ID")
    client_id = _resolve_secret(cfg.get("client_id"), fallback_env="POWERBI_CLIENT_ID")
    client_secret = _resolve_secret(cfg.get("client_secret"), fallback_env="POWERBI_CLIENT_SECRET")
    if not tenant_id or not client_id or not client_secret:
        raise ValueError(
            "Missing Power BI credentials. Provide powerbi_service.access_token or "
            "tenant_id/client_id/client_secret, or set POWERBI_ACCESS_TOKEN / "
            "POWERBI_TENANT_ID / POWERBI_CLIENT_ID / POWERBI_CLIENT_SECRET."
        )

    token_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    body = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": POWERBI_SCOPE,
        }
    ).encode("utf-8")
    request = Request(
        token_endpoint,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=_safe_timeout(cfg.get("auth_timeout_seconds"), default=60)) as response:
        payload = json.loads(response.read().decode("utf-8"))

    token = _optional_str(payload.get("access_token"))
    if not token:
        raise RuntimeError("Power BI token response did not include access_token.")
    return token


def _multipart_body(
    boundary: str,
    field_name: str,
    file_name: str,
    file_bytes: bytes,
    content_type: str,
) -> bytes:
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{file_name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return header + file_bytes + footer


def _timestamped_rdl_name(base_name: str | None, timestamp_utc: str) -> str:
    stem = Path(str(base_name or "paginated_report")).stem
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "paginated_report"
    return f"{safe_stem}_{timestamp_utc}.rdl"


def _json_or_text(value: str) -> Any:
    stripped = str(value or "").strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _optional_str(value: Any, default: str | None = None) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return default


def _resolve_secret(value: Any, fallback_env: str) -> str | None:
    direct = _optional_str(value)
    if direct:
        if direct.lower().startswith("env:"):
            env_name = direct.split(":", 1)[1].strip()
            return _optional_str(os.getenv(env_name)) if env_name else None
        return direct
    return _optional_str(os.getenv(fallback_env))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    return bool(value)


def _safe_timeout(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
