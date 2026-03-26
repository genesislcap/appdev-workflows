# `jenkins-deploy.yml`

## Purpose

This reusable GitHub Actions workflow triggers a Jenkins deployment job for an AppDev package upgrade. It can also wait for the Jenkins build to complete when `testautomation_build` is enabled.

Workflow file:
- [`.github/workflows/jenkins-deploy.yml`](/C:/work/code/appdev/appdev-workflows/.github/workflows/jenkins-deploy.yml)

## How It Is Used

The workflow is designed to be called from another GitHub Actions workflow through `workflow_call`.

At a high level it does the following:
1. Checks out the requested branch.
2. Triggers a Jenkins job using `buildWithParameters`.
3. Optionally installs `jq`.
4. Optionally polls Jenkins until the latest build finishes.

## Inputs

| Input | Required | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `branch` | No | `string` | None | Branch or ref to check out before running. |
| `client` | Yes | `string` | None | Target client passed to Jenkins. |
| `environment` | Yes | `string` | None | Target environment passed to Jenkins. |
| `hosts` | No | `string` | `ALL` | Host selection passed to Jenkins. |
| `db_backup` | No | `boolean` | None | Whether to request a database backup. |
| `run_clear_codegen_cache` | No | `boolean` | None | Whether to clear the codegen cache. |
| `run_genesis_install` | No | `boolean` | None | Whether to run Genesis install. |
| `run_install_hooks` | No | `boolean` | None | Whether to run install hooks. |
| `run_remap` | No | `boolean` | None | Whether to run remap. |
| `start_server` | No | `boolean` | None | Whether to start the server after deployment. |
| `run_post_install_hooks` | No | `boolean` | None | Whether to run post-install hooks. |
| `genesis_user` | No | `string` | None | Genesis user to pass to Jenkins. |
| `environment_level` | No | `string` | None | Environment level used in the Jenkins job path. |
| `product` | No | `string` | None | Product name used in the Jenkins job path and parameters. |
| `package_version` | No | `string` | None | Package version to deploy. |
| `repotype` | No | `string` | None | Repository type passed to Jenkins. |
| `testautomation_build` | No | `boolean` | `false` | If `true`, poll Jenkins after triggering the job. |
| `deploy_custom_nginx` | No | `boolean` | `false` | Whether to request custom nginx deployment. |

## Secrets

| Secret | Required | Description |
| --- | --- | --- |
| `JENKINS_SECURITYTOKEN` | Yes | Token used to trigger the Jenkins job. |
| `JENKINS_TOKEN` | No | API token used when polling Jenkins for build status. |

## Job Behavior

### 1. Checkout

The workflow checks out the repository with full history:

```yaml
- uses: actions/checkout@v3
  with:
    fetch-depth: '0'
    ref: ${{ inputs.branch }}
```

### 2. Trigger Jenkins

The `Trigger Jenkins Job` step sends a `POST` request to Jenkins using the `buildByToken/buildWithParameters` endpoint.

The Jenkins job path is built from:
- `product`
- `environment_level`

The target job format is:

```text
AppDev-CI_CD/<product>/<environment_level>/product_<product>_upgrade
```

### 3. Optional Polling

When `testautomation_build` is `true`, the workflow:
- installs `jq`
- polls Jenkins `lastBuild/api/json`
- exits with:
  - `0` on `SUCCESS`
  - `1` on `FAILURE`
  - `2` on `ABORTED`

The polling loop currently waits 10 seconds between checks.

## Important Notes

- The workflow currently polls `lastBuild`, which means parallel Jenkins runs can cause it to observe the wrong build if multiple executions happen at the same time.
- Several parameters in the current YAML use uppercase input names such as `inputs.CLIENT` and `inputs.ENVIRONMENT`, while the declared workflow inputs are lowercase. In GitHub Actions, input names are case-sensitive.
- `JENKINS_TOKEN` is marked optional, but it is required in practice whenever `testautomation_build` is enabled.
- The job uses the fixed GitHub Actions environment `master`.

## Example Caller

```yaml
jobs:
  deploy:
    uses: ./.github/workflows/jenkins-deploy.yml
    with:
      branch: qaautomation
      client: sample-client
      environment: qa
      environment_level: qa
      product: sample-product
      package_version: 1.2.3
      testautomation_build: true
    secrets:
      JENKINS_SECURITYTOKEN: ${{ secrets.JENKINS_SECURITYTOKEN }}
      JENKINS_TOKEN: ${{ secrets.JENKINS_TOKEN }}
```
