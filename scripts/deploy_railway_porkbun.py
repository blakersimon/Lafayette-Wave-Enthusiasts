#!/usr/bin/env python3
"""
scripts/deploy_railway_porkbun.py
Full End-to-End Automated Deployment for "Lafayette Wave Enthusiasts":
1. Creates/updates GitHub repository `blakersimon/Lafayette-Wave-Enthusiasts` and pushes `main`
2. Creates Railway Project named "Lafayette Wave Enthusiasts" and deploys from GitHub
3. Generates public Railway domain & registers custom domain `lafayette-wave-enthusiasts.blake-simon.com`
4. Configures Porkbun DNS (CNAME + _railway-verify TXT) for `lafayette-wave-enthusiasts.blake-simon.com`
"""

from __future__ import annotations
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict
import httpx
from dotenv import load_dotenv

# Load ~/.env and project .env automatically
load_dotenv(Path.home() / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

APP_NAME = "Lafayette Wave Enthusiasts"
GITHUB_USER = "blakersimon"
GITHUB_REPO = "Lafayette-Wave-Enthusiasts"
ROOT_DOMAIN = "blake-simon.com"
SUBDOMAIN = "lafayette-wave-enthusiasts"
FULL_DOMAIN = f"{SUBDOMAIN}.{ROOT_DOMAIN}"

RAILWAY_GQL_URL = "https://backboard.railway.app/graphql/v2"
PORKBUN_API_BASE = "https://api.porkbun.com/api/json/v3"


def railway_gql(token: str, query: str, variables: Dict[str, Any] | None = None) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(RAILWAY_GQL_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Railway GraphQL error: {data['errors']}")
        return data.get("data", {})


def ensure_github_repo_and_push(github_token: str, project_dir: Path) -> str:
    print(f"[1/4] Creating/verifying GitHub repository {GITHUB_USER}/{GITHUB_REPO}...")
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            "https://api.github.com/user/repos",
            headers=headers,
            json={
                "name": GITHUB_REPO,
                "private": False,
                "description": f"{APP_NAME} — Coastal Louisiana Surf Forecasting & Swell Intelligence Platform",
            },
        )
        if resp.status_code in (201, 422):
            print(f"  - GitHub repository {GITHUB_USER}/{GITHUB_REPO} ready.")
        else:
            raise RuntimeError(f"GitHub API error ({resp.status_code}): {resp.text}")

    remote_url = f"https://{GITHUB_USER}:{github_token}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
    clean_remote_url = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}.git"

    subprocess.run(["git", "remote", "remove", "origin"], cwd=project_dir, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=project_dir, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main", "--force"], cwd=project_dir, check=True)
    subprocess.run(["git", "remote", "set-url", "origin", clean_remote_url], cwd=project_dir, check=True)
    print(f"  - Pushed `main` branch to {clean_remote_url}")
    return f"{GITHUB_USER}/{GITHUB_REPO}"


def deploy_to_railway(railway_token: str, full_repo: str) -> Dict[str, Any]:
    print(f"[2/4] Provisioning Railway project '{APP_NAME}' from {full_repo}...")
    create_proj_query = """
    mutation projectCreate($input: ProjectCreateInput!) {
      projectCreate(input: $input) {
        id
        name
        environments {
          edges {
            node {
              id
              name
            }
          }
        }
      }
    }
    """
    proj_data = railway_gql(railway_token, create_proj_query, {"input": {"name": APP_NAME}})
    proj = proj_data["projectCreate"]
    project_id = proj["id"]
    env_id = proj["environments"]["edges"][0]["node"]["id"]
    print(f"  - Created Railway Project '{APP_NAME}' (id={project_id}, env={env_id})")

    create_svc_query = """
    mutation serviceCreate($input: ServiceCreateInput!) {
      serviceCreate(input: $input) {
        id
        name
      }
    }
    """
    svc_data = railway_gql(
        railway_token,
        create_svc_query,
        {
            "input": {
                "projectId": project_id,
                "name": APP_NAME,
                "source": {"repo": full_repo},
            }
        },
    )
    service_id = svc_data["serviceCreate"]["id"]
    print(f"  - Created Railway Service '{APP_NAME}' (id={service_id})")

    time.sleep(4)
    sys_dom_query = """
    mutation serviceDomainCreate($input: ServiceDomainCreateInput!) {
      serviceDomainCreate(input: $input) {
        id
        domain
      }
    }
    """
    sys_dom_data = railway_gql(
        railway_token,
        sys_dom_query,
        {"input": {"environmentId": env_id, "serviceId": service_id}},
    )
    railway_domain = sys_dom_data["serviceDomainCreate"]["domain"]
    print(f"  - Generated Railway Domain: https://{railway_domain}")

    cust_dom_query = """
    mutation customDomainCreate($input: CustomDomainCreateInput!) {
      customDomainCreate(input: $input) {
        id
        domain
        status {
          dnsRecords {
            hostlabel
            requiredValue
            recordType
          }
        }
      }
    }
    """
    cust_dom_data = railway_gql(
        railway_token,
        cust_dom_query,
        {
            "input": {
                "projectId": project_id,
                "environmentId": env_id,
                "serviceId": service_id,
                "domain": FULL_DOMAIN,
            }
        },
    )
    return {
        "project_id": project_id,
        "environment_id": env_id,
        "service_id": service_id,
        "railway_domain": railway_domain,
        "custom_domain": cust_dom_data["customDomainCreate"],
    }


def configure_porkbun_subdomain(
    pb_key: str,
    pb_secret: str,
    custom_domain_info: Dict[str, Any],
    fallback_target: str,
) -> None:
    print(f"[3/4] Configuring Porkbun DNS for {FULL_DOMAIN}...")
    auth = {"apikey": pb_key, "secretapikey": pb_secret}
    dns_records = custom_domain_info.get("status", {}).get("dnsRecords", [])

    cname_target = fallback_target
    txt_host = None
    txt_val = None

    for rec in dns_records:
        rtype = rec.get("recordType", "")
        if "CNAME" in rtype and rec.get("requiredValue"):
            cname_target = rec["requiredValue"]
        elif "TXT" in rtype:
            txt_host = rec.get("hostlabel")
            txt_val = rec.get("requiredValue")

    with httpx.Client(timeout=20.0) as client:
        r_list = client.post(f"{PORKBUN_API_BASE}/dns/retrieve/{ROOT_DOMAIN}", json=auth).json()
        if r_list.get("status") == "SUCCESS":
            for existing in r_list.get("records", []):
                if existing.get("name") == FULL_DOMAIN and existing.get("type") in ("CNAME", "A", "ALIAS"):
                    rec_id = existing.get("id")
                    client.post(f"{PORKBUN_API_BASE}/dns/delete/{ROOT_DOMAIN}/{rec_id}", json=auth)

        r_cname = client.post(
            f"{PORKBUN_API_BASE}/dns/create/{ROOT_DOMAIN}",
            json={
                **auth,
                "name": SUBDOMAIN,
                "type": "CNAME",
                "content": cname_target,
                "ttl": "600",
            },
        ).json()
        print(f"  - Porkbun CNAME {FULL_DOMAIN} -> {cname_target}: {r_cname.get('status')}")

        if txt_host and txt_val:
            sub_txt = txt_host.replace(f".{ROOT_DOMAIN}", "").strip(".")
            r_txt = client.post(
                f"{PORKBUN_API_BASE}/dns/create/{ROOT_DOMAIN}",
                json={
                    **auth,
                    "name": sub_txt,
                    "type": "TXT",
                    "content": txt_val,
                    "ttl": "600",
                },
            ).json()
            print(f"  - Porkbun TXT {sub_txt} -> {txt_val}: {r_txt.get('status')}")


def main() -> int:
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    railway_token = os.environ.get("RAILWAY_API_TOKEN", "").strip()
    pb_key = os.environ.get("PORKBUN_API_KEY", "").strip()
    pb_secret = os.environ.get("PORKBUN_SECRET_KEY", "").strip()

    missing = [
        name
        for name, val in [
            ("GITHUB_TOKEN", github_token),
            ("RAILWAY_API_TOKEN", railway_token),
            ("PORKBUN_API_KEY", pb_key),
            ("PORKBUN_SECRET_KEY", pb_secret),
        ]
        if not val
    ]
    if missing:
        print(f"Missing required credentials in ~/.env: {', '.join(missing)}", file=sys.stderr)
        return 1

    project_dir = Path(__file__).resolve().parent.parent
    full_repo = ensure_github_repo_and_push(github_token, project_dir)
    rw_info = deploy_to_railway(railway_token, full_repo)
    configure_porkbun_subdomain(pb_key, pb_secret, rw_info["custom_domain"], rw_info["railway_domain"])

    print("\n[4/4] Deployment Summary:")
    print(f"  - App Name:       {APP_NAME}")
    print(f"  - GitHub Repo:    https://github.com/{full_repo}")
    print(f"  - Railway URL:    https://{rw_info['railway_domain']}")
    print(f"  - Custom Domain:  https://{FULL_DOMAIN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
