#!/usr/bin/env bash
set -euo pipefail

yq -i "${IMAGE_TAG_YQ_PATH} = \"${IMAGE_TAG}\"" \
  "${APP_PATH}/${VALUES_FILE}"
