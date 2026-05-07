#!/usr/bin/env python3
"""
Resolve SendIt workflow auth context from environment-scoped variables/secrets.

Inputs (env):
- TARGET_ACCOUNT
- TARGET_REGION (optional)
- SENDIT_DEFAULT_REGION
- SENDIT_ALLOWED_REGIONS (CSV or JSON array)
- GITHUB_OUTPUT
"""

from __future__ import annotations

import json
import os
import sys


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def parse_allowed_regions(raw: str) -> list[str]:
    value = raw.strip()
    if not value:
        return []

    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            fail(f"SENDIT_ALLOWED_REGIONS JSON parse failed: {exc}")
        if not isinstance(parsed, list):
            fail("SENDIT_ALLOWED_REGIONS JSON must be a list of regions.")
        return [str(item).strip() for item in parsed if str(item).strip()]

    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> None:
    target_account = os.environ.get("TARGET_ACCOUNT", "").strip()
    target_region = os.environ.get("TARGET_REGION", "").strip()
    default_region = os.environ.get("SENDIT_DEFAULT_REGION", "").strip()
    allowed_regions_raw = os.environ.get("SENDIT_ALLOWED_REGIONS", "").strip()
    github_output = os.environ.get("GITHUB_OUTPUT", "").strip()

    if not target_account:
        fail("TARGET_ACCOUNT is required.")
    if not default_region:
        fail(
            f"Missing SENDIT_DEFAULT_REGION variable in GitHub Environment '{target_account}'."
        )
    if not allowed_regions_raw:
        fail(
            f"Missing SENDIT_ALLOWED_REGIONS variable in GitHub Environment '{target_account}'."
        )
    if not github_output:
        fail("GITHUB_OUTPUT is not set.")

    allowed_regions = parse_allowed_regions(allowed_regions_raw)
    if not allowed_regions:
        fail("SENDIT_ALLOWED_REGIONS resolved to an empty list.")

    selected_region = target_region or default_region
    if selected_region not in allowed_regions:
        fail(
            f"Region '{selected_region}' is not allowed for account '{target_account}'. "
            f"Allowed regions: {', '.join(allowed_regions)}"
        )

    with open(github_output, "a", encoding="utf-8") as fh:
        fh.write(f"aws_region={selected_region}\n")
        fh.write(f"allowed_regions={','.join(allowed_regions)}\n")


if __name__ == "__main__":
    main()
