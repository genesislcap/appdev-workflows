#!/usr/bin/env python3
"""
Upload file(s) to the tagged data bucket under sendit/, then trigger SendIt via repository_dispatch.

Requires: AWS credentials in the environment.

For normal runs, `SENDIT_ASSUME_ROLE_ARN` must be set (full IAM role ARN for CSVSendItCrossAccountRole).

For `--dry-run` only, `SENDIT_ASSUME_ROLE_ARN` may be omitted; the script will use the default AWS credential chain for bucket discovery.

GitHub authentication is resolved in this order:
1) If `GITHUB_TOKEN` is set in the environment, it is used directly.
2) Otherwise, credentials are read from AWS Secrets Manager secret:
   `<AppName>/environment/<EnvironmentName>`

That secret may be either:
- a raw token string (legacy), or
- JSON containing either:
  - legacy PAT-style keys: `token`, `github_token`, `GITHUB_TOKEN`
  - GitHub App credential keys: `GH_APP_ID`, `GH_APP_PRIVATE_KEY`, optional `GH_APP_INSTALLATION_ID`
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3
import jwt
import requests
from botocore.exceptions import BotoCoreError, ClientError
from jwt.exceptions import InvalidKeyError


EVENT_TYPE = "sendit_requested"
WORKFLOW_FILE = "sendit-repository-dispatch.yml"
DEFAULT_GITHUB_API_VERSION = "2022-11-28"


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def assume_role_session(
    region: str,
    role_arn: str,
    session_name: str = "sendit-cli",
) -> boto3.Session:
    base = boto3.client("sts", region_name=region)
    try:
        resp = base.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            DurationSeconds=3600,
        )
    except (ClientError, BotoCoreError) as exc:
        eprint(f"Failed to assume role {role_arn}: {exc}")
        sys.exit(2)
    creds = resp["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )


def find_data_bucket(
    session: boto3.Session,
    environment: str,
    app_name: str,
) -> str:
    client = session.client("resourcegroupstaggingapi")
    try:
        resp = client.get_resources(
            TagFilters=[
                {"Key": "EnvironmentName", "Values": [environment]},
                {"Key": "AppName", "Values": [app_name]},
                {"Key": "Type", "Values": ["data"]},
            ],
            ResourceTypeFilters=["s3"],
        )
    except (ClientError, BotoCoreError) as exc:
        eprint(f"Resource Groups Tagging API lookup failed: {exc}")
        sys.exit(3)
    items = resp.get("ResourceTagMappingList") or []
    if not items:
        eprint(
            "No S3 bucket found for tags "
            f"EnvironmentName={environment}, AppName={app_name}, Type=data"
        )
        sys.exit(4)
    arn = items[0]["ResourceARN"]
    # arn:aws:s3:::bucket-name
    parts = arn.split(":")
    if len(parts) < 6 or parts[2] != "s3":
        eprint(f"Unexpected bucket ARN: {arn}")
        sys.exit(4)
    return parts[5]


def upload_files(
    session: boto3.Session,
    bucket: str,
    local_paths: list[Path],
) -> list[str]:
    s3 = session.client("s3")
    keys: list[str] = []
    for path in local_paths:
        key = f"sendit/{path.name}"
        try:
            s3.upload_file(str(path), bucket, key)
        except (ClientError, BotoCoreError) as exc:
            eprint(f"S3 upload failed for {path}: {exc}")
            sys.exit(5)
        keys.append(key)
    return keys


def _session_payload_credentials(session: boto3.Session, fallback_region: str) -> dict[str, str]:
    creds = session.get_credentials()
    if creds is None:
        eprint("Unable to resolve AWS credentials from the active session for payload forwarding.")
        sys.exit(1)
    frozen = creds.get_frozen_credentials()
    if not frozen.access_key or not frozen.secret_key:
        eprint("Active AWS session credentials are incomplete; cannot build dispatch payload credentials.")
        sys.exit(1)

    payload_creds: dict[str, str] = {
        "aws_access_key_id": frozen.access_key,
        "aws_secret_access_key": frozen.secret_key,
        "aws_region": session.region_name or fallback_region,
    }
    return payload_creds


@dataclass(frozen=True)
class GithubAppCredentials:
    app_id: str
    private_key_pem: str
    installation_id: str | None = None


def _github_api_version() -> str:
    return os.environ.get("GITHUB_API_VERSION", DEFAULT_GITHUB_API_VERSION).strip() or DEFAULT_GITHUB_API_VERSION


def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _github_api_version(),
    }


def _normalize_private_key_pem(value: str) -> str:
    """Normalize private key material from common Secrets Manager formats.

    Supported input forms:
    - Plain PEM with real newlines
    - PEM with escaped newlines (\n)
    - JSON-escaped string value (wrapped in quotes)
    - base64-encoded PEM text
    """
    raw = value.strip()

    # If value is itself a JSON string literal, unescape it first.
    if raw.startswith('"') and raw.endswith('"'):
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, str):
                raw = decoded.strip()
        except json.JSONDecodeError:
            pass

    # Normalize escaped newlines and CRLF.
    raw = raw.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").strip()

    if "BEGIN" in raw and "PRIVATE KEY" in raw:
        return raw

    # Try base64-decoded PEM if caller stored key in base64 form.
    try:
        maybe_pem = base64.b64decode(raw, validate=True).decode("utf-8").strip()
        maybe_pem = maybe_pem.replace("\r\n", "\n")
        if "BEGIN" in maybe_pem and "PRIVATE KEY" in maybe_pem:
            return maybe_pem
    except Exception:
        pass

    return raw


def _parse_secret_string(secret_string: str) -> tuple[dict[str, Any] | None, str | None]:
    stripped = secret_string.strip()
    if not stripped.startswith("{"):
        return None, secret_string
    try:
        payload = json.loads(secret_string)
    except json.JSONDecodeError:
        return None, secret_string
    if not isinstance(payload, dict):
        return None, secret_string
    return payload, None


def _extract_legacy_pat(payload: dict[str, Any]) -> str | None:
    for key in ("token", "github_token", "GITHUB_TOKEN"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_github_app_credentials(payload: dict[str, Any]) -> GithubAppCredentials | None:
    app_id = payload.get("GH_APP_ID")
    private_key = payload.get("GH_APP_PRIVATE_KEY")
    installation_id = payload.get("GH_APP_INSTALLATION_ID")

    if not isinstance(app_id, str) or not app_id.strip():
        return None
    if not isinstance(private_key, str) or not private_key.strip():
        return None

    inst: str | None = None
    if isinstance(installation_id, str) and installation_id.strip():
        inst = installation_id.strip()
    elif isinstance(installation_id, int):
        inst = str(installation_id)

    return GithubAppCredentials(
        app_id=app_id.strip(),
        private_key_pem=_normalize_private_key_pem(private_key),
        installation_id=inst,
    )


def _mint_github_app_jwt(app_id: str, private_key_pem: str) -> str:
    now = datetime.now(timezone.utc)
    issued_at = now - timedelta(seconds=30)
    expires_at = now + timedelta(minutes=9)
    payload = {
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": app_id,
    }
    try:
        return jwt.encode(payload, private_key_pem, algorithm="RS256")
    except InvalidKeyError as exc:
        eprint(
            "Invalid GH_APP_PRIVATE_KEY format. "
            "Expected a valid GitHub App private key PEM (or escaped/base64 PEM)."
        )
        eprint(f"JWT signing failed: {exc}")
        sys.exit(1)


def _github_get_json(url: str, bearer: str) -> Any:
    r = requests.get(url, headers=github_headers(bearer), timeout=60)
    if r.status_code != 200:
        eprint(f"GitHub GET failed: HTTP {r.status_code} {r.text} ({url})")
        sys.exit(7)
    return r.json()


def _github_post_json(url: str, bearer: str, body: dict[str, Any] | None) -> Any:
    r = requests.post(
        url,
        headers=github_headers(bearer),
        json=body if body is not None else {},
        timeout=60,
    )
    if r.status_code not in (200, 201, 204):
        eprint(f"GitHub POST failed: HTTP {r.status_code} {r.text} ({url})")
        sys.exit(7)
    if r.status_code == 204 or not r.text.strip():
        return None
    return r.json()


def _parse_owner_repo(owner_repo: str) -> tuple[str, str]:
    parts = owner_repo.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        eprint(f"Invalid --github-repository value: {owner_repo} (expected owner/repo)")
        sys.exit(1)
    return parts[0], parts[1]


def _resolve_installation_id(
    app_jwt: str,
    owner_repo: str,
    api_url: str,
) -> str:
    owner, repo = _parse_owner_repo(owner_repo)
    url = f"{api_url.rstrip('/')}/repos/{owner}/{repo}/installation"
    data = _github_get_json(url, app_jwt)
    installation_id = data.get("id")
    if installation_id is None:
        eprint(f"Unexpected installation response (missing id): {data}")
        sys.exit(7)
    return str(installation_id)


def _mint_installation_access_token(
    app_jwt: str,
    installation_id: str,
    api_url: str,
) -> str:
    url = f"{api_url.rstrip('/')}/app/installations/{installation_id}/access_tokens"
    data = _github_post_json(url, app_jwt, {})
    if not isinstance(data, dict):
        eprint(f"Unexpected access token response: {data}")
        sys.exit(7)
    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        eprint(f"Unexpected access token response (missing token): {data}")
        sys.exit(7)
    return token.strip()


def mint_github_token_from_secret_payload(
    secret_string: str,
    owner_repo: str,
    api_url: str,
) -> str:
    payload, raw = _parse_secret_string(secret_string)
    if payload is None:
        if not raw or not raw.strip():
            eprint("GitHub secret is empty.")
            sys.exit(1)
        return raw.strip()

    legacy = _extract_legacy_pat(payload)
    app_creds = _extract_github_app_credentials(payload)

    if app_creds is not None:
        app_jwt = _mint_github_app_jwt(app_creds.app_id, app_creds.private_key_pem)
        installation_id = app_creds.installation_id
        if installation_id is None:
            installation_id = _resolve_installation_id(app_jwt, owner_repo, api_url)
        return _mint_installation_access_token(app_jwt, installation_id, api_url)

    if legacy is not None:
        return legacy

    # JSON secret but no recognized auth keys.
    eprint(
        "GitHub secret JSON did not contain recognizable auth fields. "
        "Expected one of: token/github_token/GITHUB_TOKEN, "
        "or GH_APP_ID + GH_APP_PRIVATE_KEY (+ optional GH_APP_INSTALLATION_ID)."
    )
    sys.exit(1)


def load_github_token(
    session: boto3.Session,
    secret_id: str,
    owner_repo: str,
    api_url: str,
) -> str:
    env_token = os.environ.get("GITHUB_TOKEN")
    if isinstance(env_token, str) and env_token.strip():
        return env_token.strip()

    sm = session.client("secretsmanager")
    try:
        resp = sm.get_secret_value(SecretId=secret_id)
    except (ClientError, BotoCoreError) as exc:
        eprint(f"Failed to read GitHub auth secret {secret_id}: {exc}")
        sys.exit(1)

    secret_string = resp.get("SecretString")
    if not secret_string:
        eprint(
            f"Secret {secret_id} has no SecretString value. Binary secrets are not supported by this script."
        )
        sys.exit(1)

    return mint_github_token_from_secret_payload(secret_string, owner_repo=owner_repo, api_url=api_url)


def dispatch_repository_event(
    token: str,
    owner_repo: str,
    client_payload: dict[str, Any],
    api_url: str,
) -> None:
    url = f"{api_url.rstrip('/')}/repos/{owner_repo}/dispatches"
    body = {"event_type": EVENT_TYPE, "client_payload": client_payload}
    r = requests.post(
        url,
        headers=github_headers(token),
        json=body,
        timeout=60,
    )
    if r.status_code != 204:
        eprint(f"repository_dispatch failed: HTTP {r.status_code} {r.text}")
        sys.exit(6)


def _parse_github_time(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def poll_workflow_run(
    token: str,
    owner_repo: str,
    dispatch_started: datetime,
    wait_timeout_s: int,
    api_url: str,
) -> str:
    """Find the workflow run started after dispatch, then poll it until completed."""
    base = f"{api_url.rstrip('/')}/repos/{owner_repo}"
    wf_url = f"{base}/actions/workflows/{WORKFLOW_FILE}/runs"
    deadline = time.monotonic() + wait_timeout_s
    cutoff = dispatch_started.astimezone(timezone.utc) - timedelta(seconds=5)
    our_run_id: int | None = None
    hdrs = github_headers(token)

    while time.monotonic() < deadline:
        if our_run_id is None:
            r = requests.get(
                wf_url,
                headers=hdrs,
                params={
                    "event": "repository_dispatch",
                    "per_page": "30",
                },
                timeout=60,
            )
            if r.status_code != 200:
                eprint(f"List workflow runs failed: HTTP {r.status_code} {r.text}")
                sys.exit(7)
            data = r.json()
            runs = data.get("workflow_runs") or []
            candidates = [
                run
                for run in runs
                if _parse_github_time(run["created_at"]) >= cutoff
            ]
            if candidates:
                our_run = max(candidates, key=lambda x: int(x["run_number"]))
                our_run_id = int(our_run["id"])
        else:
            r = requests.get(f"{base}/actions/runs/{our_run_id}", headers=hdrs, timeout=60)
            if r.status_code != 200:
                eprint(f"Get workflow run failed: HTTP {r.status_code} {r.text}")
                sys.exit(7)
            run = r.json()
            if run["status"] == "completed":
                conclusion = run.get("conclusion") or "unknown"
                return str(conclusion)
        time.sleep(15)

    eprint("Timed out waiting for workflow run to complete.")
    sys.exit(8)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Upload files to S3 sendit/ prefix and trigger SendIt via repository_dispatch."
    )
    p.add_argument("--app-name", required=True, help="AppName tag value on the data bucket")
    p.add_argument(
        "--environment",
        required=True,
        help="EnvironmentName tag value on the data bucket",
    )
    p.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[],
        help="One or more local files to upload (object key sendit/<basename>)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve bucket and validate GitHub auth secret access; do not upload, dispatch, or poll",
    )
    p.add_argument(
        "--no-wait",
        action="store_true",
        help="After dispatch, do not poll for workflow completion",
    )
    p.add_argument(
        "--wait-timeout",
        type=int,
        default=2700,
        help="Seconds to wait for workflow completion (default: 2700)",
    )
    p.add_argument(
        "--github-repository",
        default=os.environ.get("GITHUB_REPOSITORY", "genesislcap/appdev-workflows"),
        help="owner/repo for dispatches (default: env GITHUB_REPOSITORY or genesislcap/appdev-workflows)",
    )
    p.add_argument(
        "--github-api-url",
        default=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        help="GitHub API base URL (for GHES)",
    )
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        eprint("AWS_REGION or AWS_DEFAULT_REGION must be set.")
        sys.exit(1)

    role_arn = os.environ.get("SENDIT_ASSUME_ROLE_ARN")
    if not role_arn and not args.dry_run:
        eprint("SENDIT_ASSUME_ROLE_ARN must be set (full IAM role ARN for CSVSendItCrossAccountRole).")
        sys.exit(1)

    for path in args.paths:
        if not path.is_file():
            eprint(f"Not a file: {path}")
            sys.exit(1)

    if args.dry_run:
        if role_arn:
            session = boto3.Session(region_name=region)
        else:
            session = boto3.Session(region_name=region)
        bucket = find_data_bucket(session, args.environment, args.app_name)
        print(f"Resolved bucket: {bucket}")
        secret_id = f"{args.app_name}/environment/{args.environment}"
        base_session = boto3.Session(region_name=region)
        token = load_github_token(
            base_session,
            secret_id,
            owner_repo=args.github_repository,
            api_url=args.github_api_url,
        )
        print(
            f"Validated GitHub auth from secret/env for secret id: {secret_id} "
            f"(token length={len(token)})"
        )
        for path in args.paths:
            print(f"[dry-run] would upload {path} -> s3://{bucket}/sendit/{path.name}")
        print("[dry-run] would trigger repository_dispatch")
        return

    if not args.paths:
        eprint("At least one file path is required unless using --dry-run.")
        sys.exit(1)

    session = assume_role_session(region, role_arn)
    bucket = find_data_bucket(session, args.environment, args.app_name)
    print(f"Resolved bucket: {bucket}")

    keys = upload_files(session, bucket, args.paths)
    for k in keys:
        print(f"Uploaded s3://{bucket}/{k}")

    secret_id = f"{args.app_name}/environment/{args.environment}"
    base_session = boto3.Session(region_name=region)
    token = load_github_token(
        base_session,
        secret_id,
        owner_repo=args.github_repository,
        api_url=args.github_api_url,
    )
    print(f"Resolved GitHub auth (secret id: {secret_id}, env override supported)")
    if not token:
        eprint("No GitHub token resolved.")
        sys.exit(1)

    correlation_id = str(uuid.uuid4())

    client_payload: dict[str, Any] = {
        "correlation_id": correlation_id,
        "app_name": args.app_name,
        "environment": args.environment,
        "uploaded_keys": keys,
    }
    client_payload.update(_session_payload_credentials(session, region))
    print(f"Dispatching {EVENT_TYPE} correlation_id={correlation_id}")
    dispatch_started = datetime.now(timezone.utc)
    dispatch_repository_event(
        token,
        args.github_repository,
        client_payload,
        args.github_api_url,
    )
    print("repository_dispatch accepted (HTTP 204).")

    if args.no_wait:
        return

    conclusion = poll_workflow_run(
        token,
        args.github_repository,
        dispatch_started,
        args.wait_timeout,
        args.github_api_url,
    )
    print(f"Workflow completed with conclusion={conclusion}")
    if conclusion != "success":
        sys.exit(9)


if __name__ == "__main__":
    main()
