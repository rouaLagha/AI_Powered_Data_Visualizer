from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def publish_workbook_if_configured(
    workbook_path: str | Path,
    tableau_config: dict | None,
) -> dict:
    path = Path(workbook_path)

    if not isinstance(tableau_config, dict) or not bool(tableau_config.get("enabled")):
        return {
            "status": "skipped",
            "reason": "tableau_server publishing is disabled",
            "workbook_path": str(path),
        }

    if not path.exists():
        raise FileNotFoundError(f"Workbook file not found: {path}")

    return _publish_workbook(path=path, cfg=tableau_config)


def _publish_workbook(path: Path, cfg: dict) -> dict:
    try:
        import tableauserverclient as TSC
    except ImportError as exc:
        raise RuntimeError(
            "tableauserverclient is required for Tableau publishing. "
            "Install it with: pip install tableauserverclient"
        ) from exc

    server_url = _required_str(cfg, "server_url")
    site_content_url = _optional_str(cfg.get("site_content_url"), default="")
    auth_method = _optional_str(
        cfg.get("auth_method") or cfg.get("auth_type"),
        default="pat",
    ).lower()
    file_format = _optional_str(cfg.get("file_format"), default="auto").lower()
    file_extension = path.suffix.lower().lstrip(".")

    if file_extension not in {"twb", "twbx"}:
        raise ValueError(
            "Unsupported publish artifact extension. Expected .twb or .twbx."
        )
    if file_format not in {"auto", "twb", "twbx"}:
        raise ValueError(
            "Invalid tableau_server.file_format. Use 'auto', 'twb', or 'twbx'."
        )

    if "public.tableau.com" in server_url.lower():
        return {
            "status": "manual_upload_required",
            "reason": "Tableau Public does not expose a compatible REST publish endpoint for TSC in this workflow.",
            "server_url": server_url,
            "site_content_url": site_content_url,
            "publish_mode": _optional_str(cfg.get("publish_mode"), default="overwrite"),
            "file_format": file_extension,
            "publish_file": str(path),
            "manual_upload_url": "https://public.tableau.com/",
        }

    project_id = _optional_str(cfg.get("project_id"))
    project_name = _optional_str(cfg.get("project_name"))
    workbook_name = _optional_str(cfg.get("workbook_name"), default=path.stem)

    if auth_method == "pat":
        pat_name = _required_str(cfg, "pat_name")
        pat_secret = _resolve_secret(cfg.get("pat_secret"), fallback_env="TABLEAU_PAT_SECRET")
        if not pat_secret:
            raise ValueError(
                "Missing PAT secret. Set tableau_server.pat_secret or TABLEAU_PAT_SECRET env var."
            )
        auth = TSC.PersonalAccessTokenAuth(
            token_name=pat_name,
            personal_access_token=pat_secret,
            site_id=site_content_url,
        )
    elif auth_method in {"username_password", "userpass", "password"}:
        username = _resolve_secret(cfg.get("username"), fallback_env="TABLEAU_USERNAME")
        password = _resolve_secret(cfg.get("password"), fallback_env="TABLEAU_PASSWORD")
        if not username or not password:
            raise ValueError(
                "Missing username/password. Set tableau_server.username and tableau_server.password "
                "or TABLEAU_USERNAME/TABLEAU_PASSWORD env vars."
            )
        auth = TSC.TableauAuth(username=username, password=password, site_id=site_content_url)
    else:
        raise ValueError(
            "Invalid tableau_server.auth_method. Use 'pat' or 'username_password'."
        )

    publish_mode = _parse_publish_mode(_optional_str(cfg.get("publish_mode"), default="overwrite"), TSC)
    skip_connection_check = bool(cfg.get("skip_connection_check", False))
    as_job = bool(cfg.get("as_job", False))

    server = TSC.Server(server_url, use_server_version=True)
    _disable_environment_proxies(server)
    try:
        server.add_http_options({"proxies": {"http": None, "https": None}})
    except Exception:
        pass

    with server.auth.sign_in(auth):
        resolved_project_id = project_id or _resolve_project_id(
            server=server,
            tsc_module=TSC,
            project_name=project_name,
        )

        workbook_item = TSC.WorkbookItem(project_id=resolved_project_id, name=workbook_name)

        published_item = _publish_with_fallback(
            server=server,
            workbook_item=workbook_item,
            workbook_path=path,
            publish_mode=publish_mode,
            as_job=as_job,
            skip_connection_check=skip_connection_check,
        )

    return {
        "status": "published",
        "server_url": server_url,
        "site_content_url": site_content_url,
        "project_id": resolved_project_id,
        "workbook_name": getattr(published_item, "name", workbook_name),
        "workbook_id": getattr(published_item, "id", None),
        "workbook_content_url": getattr(published_item, "content_url", None),
        "workbook_webpage_url": getattr(published_item, "webpage_url", None),
        "published_file": str(path),
        "file_format": file_extension,
        "publish_mode": _optional_str(cfg.get("publish_mode"), default="overwrite"),
    }


def _disable_environment_proxies(server: Any) -> None:
    session = getattr(server, "session", None)
    if session is None:
        return
    try:
        session.trust_env = False
    except Exception:
        pass
    try:
        session.proxies.clear()
    except Exception:
        pass


def _publish_with_fallback(
    server: Any,
    workbook_item: Any,
    workbook_path: Path,
    publish_mode: Any,
    as_job: bool,
    skip_connection_check: bool,
) -> Any:
    try:
        return server.workbooks.publish(
            workbook_item,
            str(workbook_path),
            publish_mode,
            as_job=as_job,
            skip_connection_check=skip_connection_check,
        )
    except TypeError:
        return server.workbooks.publish(
            workbook_item,
            str(workbook_path),
            publish_mode,
            as_job=as_job,
        )


def _resolve_project_id(server: Any, tsc_module: Any, project_name: str | None) -> str:
    projects = [project for project in tsc_module.Pager(server.projects)]

    if not projects:
        raise ValueError("No Tableau projects available for the authenticated user.")

    if not project_name:
        if len(projects) == 1:
            return projects[0].id
        raise ValueError(
            "Missing Tableau project destination. Set tableau_server.project_id or tableau_server.project_name."
        )

    matches = [project for project in projects if getattr(project, "name", None) == project_name]
    if not matches:
        project_name_lower = project_name.lower()
        matches = [
            project
            for project in projects
            if isinstance(getattr(project, "name", None), str)
            and getattr(project, "name").lower() == project_name_lower
        ]

    if not matches:
        if len(projects) == 1:
            return projects[0].id
        raise ValueError(f"Tableau project not found: {project_name}")

    if len(matches) > 1:
        raise ValueError(
            "Multiple Tableau projects share the same name. "
            "Set tableau_server.project_id to disambiguate."
        )

    return matches[0].id


def _parse_publish_mode(mode: str, tsc_module: Any) -> Any:
    normalized = mode.strip().lower()
    if normalized in {"overwrite", "replace"}:
        return tsc_module.Server.PublishMode.Overwrite
    if normalized in {"create_new", "createnew", "new", "create"}:
        return tsc_module.Server.PublishMode.CreateNew

    raise ValueError(
        "Invalid tableau_server.publish_mode. Use 'overwrite' or 'create_new'."
    )


def _required_str(cfg: dict, key: str) -> str:
    value = _optional_str(cfg.get(key))
    if not value:
        raise ValueError(f"Missing required tableau_server field: {key}")
    return value


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
            if not env_name:
                return None
            return _optional_str(os.getenv(env_name))
        return direct

    return _optional_str(os.getenv(fallback_env))
