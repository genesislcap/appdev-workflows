#!/usr/bin/env bash
set -euo pipefail

git config user.name "$GIT_USER_NAME"
git config user.email "$GIT_USER_EMAIL"

if git diff --quiet -- "$APP_PATH"; then
  echo "No change under $APP_PATH, tag already $IMAGE_TAG"
  echo "changed=false" >> "$GITHUB_OUTPUT"
  echo "commit-sha=" >> "$GITHUB_OUTPUT"
  exit 0
fi

MESSAGE="${COMMIT_MESSAGE:-chore($APP_PATH): bump image tag to $IMAGE_TAG}"

git add "$APP_PATH"
git commit -m "$MESSAGE"
git push origin "HEAD:$BRANCH"

echo "changed=true" >> "$GITHUB_OUTPUT"
echo "commit-sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"
