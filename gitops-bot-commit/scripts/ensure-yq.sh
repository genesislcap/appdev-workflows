#!/usr/bin/env bash
set -euo pipefail

if ! command -v yq >/dev/null 2>&1; then
  sudo wget -q https://github.com/mikefarah/yq/releases/download/v4.27.3/yq_linux_amd64 -O /usr/bin/yq
  sudo chmod +x /usr/bin/yq
fi
