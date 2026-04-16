#!/usr/bin/env python3
"""
Upload file(s) to the tagged data bucket under sendit/, then trigger SendIt via repository_dispatch.

Requires: AWS credentials in the environment and SENDIT_ASSUME_ROLE_ARN.
GitHub token is loaded from AWS Secrets Manager secret:
<AppName>/environment/<EnvironmentName>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError


EVENT_TYPE = "sendit_requested"
WORKFLOW_FILE = "sendit-repository-dispatch.yml"


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


def load_github_token_from_secret(
    session: boto3.Session,
    secret_id: str,
) -> str:
    sm = session.client("secretsmanager")
    try:
        resp = sm.get_secret_value(SecretId=secret_id)
    except (ClientError, BotoCoreError) as exc:
        eprint(f"Failed to read GitHub token secret {secret_id}: {exc}")
        sys.exit(1)

    secret_string = resp.get("SecretString")
    if not secret_string:
        eprint(
            f"Secret {secret_id} has no SecretString value. Binary secrets are not supported by this script."
        )
        sys.exit(1)

    # Support either raw token value or common JSON keys.
    stripped = secret_string.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(secret_string)
        except json.JSONDecodeError:
            # If this looks like JSON but is malformed, fall back to raw value.
            return secret_string
        for key in ("token", "github_token", "GITHUB_TOKEN"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return secret_string


def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


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
        nargs="+",
        type=Path,
        help="One or more local files to upload (object key sendit/<basename>)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve bucket and validate token secret access; do not upload, dispatch, or poll",
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
    if not role_arn:
        eprint("SENDIT_ASSUME_ROLE_ARN must be set (full IAM role ARN for CSVSendItCrossAccountRole).")
        sys.exit(1)

    for path in args.paths:
        if not path.is_file():
            eprint(f"Not a file: {path}")
            sys.exit(1)

    if args.dry_run:
        session = assume_role_session(region, role_arn)
        bucket = find_data_bucket(session, args.environment, args.app_name)
        print(f"Resolved bucket: {bucket}")
        secret_id = f"{args.app_name}/environment/{args.environment}"
        base_session = boto3.Session(region_name=region)
        token = load_github_token_from_secret(base_session, secret_id)
        print(
            f"Validated GitHub token retrieval from Secrets Manager secret: {secret_id} "
            f"(length={token})"
        )
        for path in args.paths:
            print(f"[dry-run] would upload {path} -> s3://{bucket}/sendit/{path.name}")
        print("[dry-run] would trigger repository_dispatch")
        return

    session = assume_role_session(region, role_arn)
    bucket = find_data_bucket(session, args.environment, args.app_name)
    print(f"Resolved bucket: {bucket}")

    keys = upload_files(session, bucket, args.paths)
    for k in keys:
        print(f"Uploaded s3://{bucket}/{k}")

    secret_id = f"{args.app_name}/environment/{args.environment}"
    base_session = boto3.Session(region_name=region)
    token = load_github_token_from_secret(base_session, secret_id)
    print(f"Loaded GitHub token from Secrets Manager secret: {secret_id}")
    if not token:
        eprint("No GitHub token found in Secrets Manager.")
        sys.exit(1)

    correlation_id = str(uuid.uuid4())

    client_payload: dict[str, Any] = {
        "correlation_id": correlation_id,
        "app_name": args.app_name,
        "environment": args.environment,
        "uploaded_keys": keys,
    }
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
