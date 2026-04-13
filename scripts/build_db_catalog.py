from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.rdl_to_twb.db_introspection import build_db_catalog
from src.rdl_to_twb.rdl_parser import parse_rdl_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Build DB metadata catalog from RDL datasource and used tables.")
    parser.add_argument("--rdl", required=True, help="Path to source .rdl file")
    parser.add_argument(
        "--output",
        default="output/db_catalog.json",
        help="Output JSON path for DB catalog",
    )
    parser.add_argument("--datasource", default="", help="Datasource name override target (default: all found datasources)")
    parser.add_argument("--uid", default="", help="SQL auth user name override")
    parser.add_argument("--pwd", default="", help="SQL auth password override")
    parser.add_argument("--trusted", default="", help="Trusted connection override: true/false")
    parser.add_argument("--encrypt", default="", help="Encrypt override, e.g. Yes/No")
    parser.add_argument("--trust-server-cert", default="", help="TrustServerCertificate override, e.g. Yes/No")
    args = parser.parse_args()

    report = parse_rdl_file(Path(args.rdl))
    payload = report.to_dict()

    overrides: dict[str, dict[str, str]] = {}
    target_name = args.datasource.strip()
    ds_list = payload.get("data_sources", []) if isinstance(payload.get("data_sources"), list) else []
    if not target_name and ds_list and isinstance(ds_list[0], dict):
        first_name = ds_list[0].get("name")
        if isinstance(first_name, str) and first_name.strip():
            target_name = first_name.strip()

    override_payload: dict[str, str] = {}
    if args.uid.strip():
        override_payload["uid"] = args.uid.strip()
    if args.pwd:
        override_payload["pwd"] = args.pwd
    if args.trusted.strip():
        override_payload["trusted_connection"] = args.trusted.strip()
    if args.encrypt.strip():
        override_payload["encrypt"] = args.encrypt.strip()
    if args.trust_server_cert.strip():
        override_payload["trust_server_certificate"] = args.trust_server_cert.strip()

    if target_name and override_payload:
        overrides[target_name] = override_payload

    catalog = build_db_catalog(
        data_sources=payload.get("data_sources", []),
        data_sets=payload.get("data_sets", []),
        connection_overrides=overrides or None,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=True), encoding="utf-8")

    print(f"DB catalog written: {output_path}")
    for source in catalog.get("datasources", []):
        if not isinstance(source, dict):
            continue
        name = source.get("name")
        connected = source.get("connected")
        tables = source.get("tables") if isinstance(source.get("tables"), list) else []
        joins = source.get("join_specs") if isinstance(source.get("join_specs"), list) else []
        error = source.get("error")
        print(f"- datasource={name} connected={connected} tables={len(tables)} joins={len(joins)}")
        if error:
            print(f"  error={error}")


if __name__ == "__main__":
    main()
