#!/usr/bin/env python3
"""Publish a .rdl file to Power BI My Workspace using a user access token.

Usage examples:
  # Ensure POWERBI_ACCESS_TOKEN is set in the environment
  python backend/scripts/publish_powerbi_rdl.py --rdl "C:\path\to\report.rdl"

This script sends a multipart/form-data POST to https://api.powerbi.com/v1.0/myorg/imports
with `datasetDisplayName` set to the .rdl filename (Power BI accepts paginated reports).
"""

from __future__ import annotations

import os
import argparse
import requests
import sys
import json
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description="Publish .rdl to Power BI My Workspace using user access token")
    p.add_argument("--rdl", help="Path to .rdl file (or set RDL_PATH env)", default=os.getenv("RDL_PATH"))
    p.add_argument("--name-conflict", help="Power BI nameConflict (Abort|Overwrite|CreateOrOverwrite)", default="Abort")
    p.add_argument("--timeout", type=int, default=300)
    args = p.parse_args()

    token = os.getenv("POWERBI_ACCESS_TOKEN")
    if not token:
        print("Missing POWERBI_ACCESS_TOKEN env var.", file=sys.stderr)
        return 2

    if not args.rdl:
        print("Missing RDL path (arg --rdl or env RDL_PATH).", file=sys.stderr)
        return 2

    path = Path(args.rdl)
    if not path.exists() or path.suffix.lower() != ".rdl":
        print(f"RDL file not found or invalid: {path}", file=sys.stderr)
        return 2

    display_name = path.name
    endpoint = "https://api.powerbi.com/v1.0/myorg/imports"
    params = {
        "datasetDisplayName": display_name,
        "nameConflict": args.name_conflict,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    with path.open("rb") as fh:
        files = {"file": (display_name, fh, "application/octet-stream")}
        try:
            resp = requests.post(endpoint, headers=headers, params=params, files=files, timeout=args.timeout)
        except requests.RequestException as exc:
            print("Request failed:", exc, file=sys.stderr)
            return 3

    try:
        body = resp.json()
    except Exception:
        body = resp.text

    print("HTTP", resp.status_code)
    try:
        print(json.dumps(body, indent=2, ensure_ascii=False))
    except Exception:
        print(body)

    if 200 <= resp.status_code < 300:
        print("Publication OK — vérifie My Workspace dans Power BI.")
        return 0
    else:
        print("Publication échouée.", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
