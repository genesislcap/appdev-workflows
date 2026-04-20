# SendIt: S3 upload and repository dispatch

CLI that:

1. Assumes `SENDIT_ASSUME_ROLE_ARN` (typically `CSVSendItCrossAccountRole`) via STS.
2. Resolves the data S3 bucket using the same tags as [`sendit/action.yaml`](../../sendit/action.yaml): `EnvironmentName`, `AppName`, `Type=data`.
3. Uploads local files to `s3://<bucket>/sendit/<basename>`.
4. Sends a GitHub `repository_dispatch` with `event_type` **`sendit_requested`** to run [`.github/workflows/sendit-repository-dispatch.yml`](../../.github/workflows/sendit-repository-dispatch.yml).
5. By default, polls that workflow run until it completes (`success` exits 0; other conclusions exit 9).

## Install

```bash
cd scripts/sendit_upload_and_dispatch
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Packaging and distribution

This repository includes [`.github/workflows/build-sendit-cli.yml`](../../.github/workflows/build-sendit-cli.yml) to build standalone binaries using PyInstaller.

- Manual build trigger: GitHub Actions -> **Build SendIt CLI Binaries** -> **Run workflow**
- Release trigger: push a tag like `sendit-cli-v0.1.0` to publish release assets automatically
- Built artifacts:
  - `sendit-cli-linux-x64`
  - `sendit-cli-macos-x64`
  - `sendit-cli-windows-x64.exe`

### Why this is low-friction

Recipients do not need Python or pip. They only need:

1. Download the binary for their OS.
2. Make it executable on Unix (`chmod +x`).
3. Set required environment variables.
4. Run the binary with CLI arguments.

### Local packaging (for learning / reproducibility)

```bash
cd scripts/sendit_upload_and_dispatch
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --onefile --clean --name sendit-cli cli.py
```

Output binary will be created in `dist/`.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Yes (for upload path) | Base credentials used to call STS assume-role |
| `AWS_REGION` or `AWS_DEFAULT_REGION` | Yes | AWS region for STS, tagging API, and S3 |
| `SENDIT_ASSUME_ROLE_ARN` | Yes (except `--dry-run` without role) | Full ARN of `CSVSendItCrossAccountRole` (or equivalent) |
| `GITHUB_TOKEN` | No | Optional override. If set, the CLI uses this token directly and does **not** read GitHub auth from Secrets Manager. |
| `GITHUB_REPOSITORY` | No | Default `genesislcap/appdev-workflows` |
| `GITHUB_API_URL` | No | Default `https://api.github.com` (set for GitHub Enterprise Server) |
| `GITHUB_API_VERSION` | No | Default `2022-11-28` (GitHub `X-GitHub-Api-Version` header) |

Do **not** put AWS keys in the `client_payload`; the Actions workflow uses repository **secrets** (below).

## GitHub authentication (AWS Secrets Manager)

By default, the CLI reads GitHub auth from AWS Secrets Manager secret:

- `<AppName>/environment/<EnvironmentName>`

That secret may be:

1. A **raw string** token (legacy), or
2. JSON with one of these supported shapes:

### Option A: GitHub App credentials (recommended)

Store durable credential material (not the short-lived installation token):

```json
{
  "GH_APP_ID": "123456",
  "GH_APP_PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----\\n...\\n-----END RSA PRIVATE KEY-----",
  "GH_APP_INSTALLATION_ID": "12345678"
}
```

Notes:

- `GH_APP_INSTALLATION_ID` is optional. If omitted, the CLI discovers it via `GET /repos/{owner}/{repo}/installation` using an app JWT.
- The CLI mints a **GitHub App installation access token** at runtime (short-lived, typically about 1 hour). It is **not** written back to Secrets Manager.


### Option B: PAT-style token (legacy)

```json
{
  "token": "ghp_..."
}
```

Also accepted keys: `github_token`, `GITHUB_TOKEN`.

### Permissions / GitHub App settings

Regardless of whether you use a PAT or a GitHub App installation token, the effective token must be able to:

- `POST /repos/{owner}/{repo}/dispatches`
- Read workflow runs for polling (`GET` Actions endpoints)

For GitHub Apps, ensure the app is installed on the target org/account with repository access to `--github-repository`, and grant the minimum permissions required for those endpoints.

### Rotating GitHub App private keys

Rotation is an AWS Secrets Manager change:

1. Generate a new private key in the GitHub App settings.
2. Update `GH_APP_PRIVATE_KEY` in the secret JSON.
3. Optionally revoke the old private key in GitHub after confirming the CLI works.

## GitHub repository configuration (`genesislcap/appdev-workflows`)

Create these **Actions secrets** (used by the workflow when calling the SendIt composite):

| Secret | Purpose |
|--------|---------|
| `SENDIT_AWS_ACCESS_KEY_ID` | Credentials for SSM / `aws s3 sync` on the target host |
| `SENDIT_AWS_SECRET_ACCESS_KEY` | Same |
| `SENDIT_AWS_REGION` | e.g. `eu-west-1` |

## Usage

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=eu-west-1
export SENDIT_ASSUME_ROLE_ARN=arn:aws:iam::ACCOUNT:role/CSVSendItCrossAccountRole

python cli.py --app-name MY_APP --environment DEV ./data/file1.csv ./data/file2.csv
```

If installed as a package entrypoint:

```bash
pip install .
sendit-cli --app-name MY_APP --environment DEV ./data/file1.csv
```

Options:

- `--dry-run` — resolve bucket, validate GitHub auth resolution (Secrets Manager read and/or GitHub App token minting), and print intended keys; no upload, dispatch, or polling. `SENDIT_ASSUME_ROLE_ARN` is optional in this mode.
- `--no-wait` — do not poll for workflow completion after dispatch.
- `--wait-timeout SECONDS` — default `2700` (45 minutes).
- `--github-repository owner/repo` — override dispatch target.

## `repository_dispatch` contract

- **`event_type`:** `sendit_requested` (must match the workflow).
- **`client_payload` JSON fields:**
  - `correlation_id` (string, UUID) — tracing only.
  - `app_name` (string) — must match bucket tag `AppName`.
  - `environment` (string) — must match bucket tag `EnvironmentName`.
  - `uploaded_keys` (array of strings) — S3 keys under `sendit/` (informational).

The HTTP response to `POST .../dispatches` is **204** when the event is accepted; it does not wait for SendIt/SSM. Use default wait behavior or check the run in the Actions UI.

## Exit codes

| Code | Meaning |
|------|---------|
| 1 | Missing `AWS_REGION` / invalid paths |
| 2 | STS assume-role failed |
| 3 | Tagging API error |
| 4 | No bucket for tags |
| 5 | S3 upload failed |
| 6 | `repository_dispatch` non-204 |
| 7 | GitHub API list/get run failed |
| 8 | Wait timeout |
| 9 | Workflow completed with non-success conclusion |
