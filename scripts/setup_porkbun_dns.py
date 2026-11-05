#!/usr/bin/env python3
"""
scripts/setup_porkbun_dns.py
Automated Porkbun API v3 DNS configuration utility for Louisiana Surf Finder.
Configures ALIAS (root), CNAME (www), and optional TXT verification records
pointing to a Railway deployment target.

Usage:
  export PORKBUN_API_KEY="pk1_..."
  export PORKBUN_SECRET_KEY="sk1_..."
  python3 scripts/setup_porkbun_dns.py --domain louisianasurffinder.com --target your-service.up.railway.app
"""

from __future__ import annotations
import argparse
import os
import sys
from typing import Any, Dict, List, Optional
import httpx

PORKBUN_API_BASE = "https://api.porkbun.com/api/json/v3"


def configure_porkbun_dns(
    domain: str,
    railway_target: str,
    api_key: str,
    secret_key: str,
    txt_name: Optional[str] = None,
    txt_value: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Idempotently configures Porkbun DNS records for a Railway custom domain:
    1. Removes conflicting root A/ALIAS and www A/CNAME records.
    2. Creates ALIAS record on root (@) -> railway_target.
    3. Creates CNAME record on www -> railway_target.
    4. Optionally creates _railway-verify TXT record if provided.
    """
    auth = {"apikey": api_key, "secretapikey": secret_key}
    clean_target = railway_target.replace("https://", "").replace("http://", "").strip("/")
    actions_log: List[str] = []

    with httpx.Client(timeout=20.0) as client:
        # 1. Fetch existing DNS records
        resp = client.post(f"{PORKBUN_API_BASE}/dns/retrieve/{domain}", json=auth)
        data = resp.json()
        if data.get("status") != "SUCCESS":
            raise RuntimeError(f"Porkbun retrieve failed for {domain}: {data}")

        existing_records = data.get("records", [])
        for rec in existing_records:
            rec_name = rec.get("name", "")
            rec_type = rec.get("type", "")
            rec_id = rec.get("id")
            if rec_name == domain and rec_type in ("A", "ALIAS", "CNAME"):
                client.post(f"{PORKBUN_API_BASE}/dns/delete/{domain}/{rec_id}", json=auth)
                actions_log.append(f"Deleted existing root {rec_type} record (id={rec_id})")
            elif rec_name == f"www.{domain}" and rec_type in ("A", "ALIAS", "CNAME"):
                client.post(f"{PORKBUN_API_BASE}/dns/delete/{domain}/{rec_id}", json=auth)
                actions_log.append(f"Deleted existing www {rec_type} record (id={rec_id})")

        # 2. Create ALIAS record for root domain
        alias_payload = {
            **auth,
            "name": "",
            "type": "ALIAS",
            "content": clean_target,
            "ttl": "600",
        }
        r_alias = client.post(f"{PORKBUN_API_BASE}/dns/create/{domain}", json=alias_payload).json()
        actions_log.append(f"Created root ALIAS -> {clean_target}: {r_alias.get('status')}")

        # 3. Create CNAME record for www subdomain
        cname_payload = {
            **auth,
            "name": "www",
            "type": "CNAME",
            "content": clean_target,
            "ttl": "600",
        }
        r_cname = client.post(f"{PORKBUN_API_BASE}/dns/create/{domain}", json=cname_payload).json()
        actions_log.append(f"Created www CNAME -> {clean_target}: {r_cname.get('status')}")

        # 4. Optional TXT verification record
        if txt_name and txt_value:
            sub_name = txt_name.replace(f".{domain}", "").replace(domain, "").strip(".")
            txt_payload = {
                **auth,
                "name": sub_name,
                "type": "TXT",
                "content": txt_value,
                "ttl": "600",
            }
            r_txt = client.post(f"{PORKBUN_API_BASE}/dns/create/{domain}", json=txt_payload).json()
            actions_log.append(f"Created TXT ({sub_name}) -> {txt_value}: {r_txt.get('status')}")

    return {
        "domain": domain,
        "target": clean_target,
        "actions": actions_log,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure Porkbun DNS for Railway hosting.")
    parser.add_argument("--domain", required=True, help="Root domain on Porkbun (e.g. louisianasurffinder.com)")
    parser.add_argument("--target", required=True, help="Railway service domain (e.g. xxx.up.railway.app)")
    parser.add_argument("--txt-name", default=None, help="Optional _railway-verify TXT host")
    parser.add_argument("--txt-value", default=None, help="Optional _railway-verify TXT value")
    args = parser.parse_args()

    api_key = os.environ.get("PORKBUN_API_KEY", "").strip()
    secret_key = os.environ.get("PORKBUN_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        print("Error: PORKBUN_API_KEY and PORKBUN_SECRET_KEY environment variables are required.", file=sys.stderr)
        return 1

    result = configure_porkbun_dns(
        domain=args.domain,
        railway_target=args.target,
        api_key=api_key,
        secret_key=secret_key,
        txt_name=args.txt_name,
        txt_value=args.txt_value,
    )
    print(f"Porkbun DNS configuration complete for {result['domain']} -> {result['target']}:")
    for line in result["actions"]:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
