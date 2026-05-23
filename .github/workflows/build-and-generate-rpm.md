# `build-and-generate-rpm.yml`

## Purpose

This reusable workflow combines the shared Gradle build flow and the RPM packaging flow into one local end-to-end path.

It is intended to replace the old two-step process where:
1. the server/web packages were built and uploaded to JFrog Artifactory, and
2. the RPM workflow downloaded those same packages back from JFrog to package the RPM.

The new workflow keeps the generated server/web packages local in the same run and removes only that JFrog round trip.

Workflow file:
- [`.github/workflows/build-and-generate-rpm.yml`](/C:/work/code/appdev-workflows/.github/workflows/build-and-generate-rpm.yml)

## How It Is Used

The workflow is designed to be called from another GitHub Actions workflow through `workflow_call`.

At a high level it does the following:
1. Checks out the caller repository.
2. Optionally runs the `testautomation_build` preparation flow.
3. Checks out the shared `appdev-workflows` repository for the RPM spec file.
4. Sets up JDK, Node, JFrog credentials, Gradle properties, and `.npmrc`.
5. Runs the JFrog Xray scan and SAST scan.
6. Builds the server and web packages locally.
7. Optionally runs tests, coverage, Docker build/push, and Artifactory publish.
8. Optionally generates the app distribution zip path used by the older build flow.
9. Stages the generated server and web packages locally for RPM creation.
10. Optionally downloads the scan reports produced by the same workflow run and bundles them into the RPM.
11. Optionally adds the site-specific RPM input package.
12. Builds the RPM locally.
13. Uploads the final RPM to S3.

## What Makes It Different

The only intentional behavior removed from the old two-step flow is the JFrog handoff between the server/web build and the RPM build.

This means the workflow:
- does **not** upload the generated server/web packages to JFrog Artifactory for later reuse
- does **not** download those same packages back from JFrog for the RPM stage

Everything else from the build and RPM flows is preserved.

## Inputs

| Input | Required | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `branch` | No | `string` | Current ref | Branch or ref to check out from the caller repository. |
| `version` | Yes | `string` | None | Version string used in build and RPM naming. |
| `client-name` | No | `string` | Empty | Client name used for client-specific build and RPM behavior. |
| `product_name` | Yes | `string` | None | Product name used in build outputs and RPM naming. |
| `artifactory_deploy_locations` | Yes | `string` | None | Comma-separated list of S3 deploy locations for the RPM output. |
| `genesis-user` | Yes | `string` | None | Genesis user passed to the RPM package. |
| `repo-name` | No | `string` | Derived from repo name | Optional repository prefix override. |
| `distribution_dir` | No | `string` | `${product_name}-distribution` | Optional distribution directory override. |
| `working-directory` | No | `string` | `.` | Working directory used for the build. |
| `server-path` | No | `string` | `server/jvm` | Server build path inside the repository. |
| `node_version` | No | `string` | `22.x` | Node.js version used for the build. |
| `java_version` | No | `string` | `17` | Java version used for the build. |
| `config-path` | No | `string` | None | Optional `.env` file path to load build config. |
| `server-build-gradle-arguments` | No | `string` | Empty | Extra Gradle args for the server build. |
| `server-install-gradle-arguments` | No | `string` | Empty | Extra Gradle args for install tasks used when staging the server package. |
| `app_module` | No | `string` | `${product_name}-app` | App module used when `use_app_dist_zip` is enabled. |
| `use-artifactory-cache` | No | `boolean` | `false` | Enables remote cache usage for Gradle if supported. |
| `push-to-artifactory-cache` | No | `boolean` | `false` | Enables pushing build outputs to the Artifactory cache if supported. |
| `spec-file-branch` | No | `string` | `qaautomation` | Branch used to fetch the RPM spec file from `appdev-workflows`. |
| `site-distribution` | No | `string` | None | Optional site-specific archive downloaded before RPM packaging. |
| `build_server` | No | `boolean` | `true` | Controls whether the RPM stage includes the server package or builds a web-only RPM. |
| `include-xray-report` | No | `boolean` | `false` | Bundles the JFrog Xray summary and full scan reports into the RPM. |
| `use_app_dist_zip` | No | `boolean` | `false` | Builds the app distribution zip form used by the older Gradle flow. |
| `skip-tests` | No | `boolean` | `false` | Skips the test/coverage branch and switches the server build to assemble mode. |
| `testautomation_build` | No | `boolean` | `false` | Enables the `testautomation_action` setup path. |
| `testautorepo` | No | `string` | None | Repo used by the testautomation setup path. |
| `testautobranch` | No | `string` | None | Branch used by the testautomation setup path. |
| `web-path` | No | `string` | `client/web` | Web package path used by the testautomation setup path. |
| `additional_file` | No | `string` | `main-repo/.github/workflows/serverversion.properties` | Additional server properties file used by the testautomation setup path. |
| `additional_webfile` | No | `string` | `main-repo/.github/workflows/webversion.properties` | Additional web properties file used by the testautomation setup path. |
| `build_docker` | No | `boolean` | `false` | Enables Docker image build/push steps. |
| `publish-to-artifactory` | No | `boolean` | `false` | Enables Gradle Artifactory publishing. |
| `sast` | No | `boolean` | `true` | Enables Bearer SAST scan and upload. |
| `build_rpm` | No | `boolean` | `true` | Enables the RPM packaging stage. |

## Secrets

| Secret | Required | Description |
| --- | --- | --- |
| `GRADLE_PROPERTIES` | Yes | Gradle properties passed through to the build. |
| `JFROG_USERNAME` | Yes | JFrog username used for login and Gradle setup. |
| `JFROG_EMAIL` | Yes | JFrog email used by the Gradle setup. |
| `JFROG_PASSWORD` | Yes | JFrog password used for login and Gradle setup. |
| `GPR_READ_TOKEN` | Yes | Token used for npm registry access. |
| `SLACK_WEBHOOK` | No | Optional Slack webhook. |
| `JFROG_NPM_AUTH_TOKEN` | No | Token used for the JFrog npm registry config. |
| `GHA_TOKEN` | Yes | Token used by the testautomation setup path. |
| `JENKINSGENESIS_SONAR` | No | Sonar token used by the test and coverage step. |

## Workflow Summary

### Shared Build Stage

The workflow:
- checks out the caller repo
- optionally runs the testautomation setup for server and web
- checks out `appdev-workflows` for the RPM spec
- prepares Gradle, Node, and JFrog access
- optionally loads `.env`
- scans the build artifact with JFrog Xray
- optionally runs SAST
- builds the server and client locally
- optionally runs tests, coverage, Docker, and Artifactory publish

### RPM Stage

When `build_rpm` is `true`, the workflow:
- stages the locally generated server and web packages
- optionally rebuilds the server package using the app distribution zip flow
- optionally downloads a site-specific package
- optionally bundles the JFrog Xray scan reports from the same workflow run into the RPM
- repackages the server and web archives into RPM inputs
- builds the RPM locally
- uploads the RPM to S3

When `build_server` is `false`, the workflow:
- skips the server build and server package staging steps
- packages the RPM in web-only mode
- still supports `include-xray-report` if the reports should be bundled

### Skip Tests

When `skip-tests` is `true`, the workflow:
- switches the server build from `build` to `assemble`
- skips the `Test and Coverage` step
- keeps the rest of the workflow structure intact so the combined flow still matches the shared build and RPM behavior

### Output

The workflow returns the generated RPM file name as a job output.

## Notes

- The server and web packages are now passed locally between the two stages.
- The workflow can optionally include the JFrog scan reports in the RPM when `include-xray-report` is enabled.
- The final RPM is still uploaded to S3.
- The workflow still uses JFrog for login, npm registry setup, Xray scanning, and the Gradle build environment.
- The JFrog Artifactory upload/download round trip for intermediate server/web packages is intentionally removed.

## Example Caller

```yaml
jobs:
  build-and-generate-rpm:
    uses: genesislcap/appdev-workflows/.github/workflows/build-and-generate-rpm.yml@qaautomation
    with:
      branch: qaautomation
      version: 1.2.3
      client-name: hsbc
      product_name: tam2
      artifactory_deploy_locations: product/tam2/rpm/hsbc/
      genesis-user: tam2
      build_rpm: true
    secrets:
      GRADLE_PROPERTIES: ${{ secrets.GRADLE_PROPERTIES }}
      JFROG_USERNAME: ${{ secrets.JFROG_USERNAME }}
      JFROG_EMAIL: ${{ secrets.JFROG_EMAIL }}
      JFROG_PASSWORD: ${{ secrets.JFROG_PASSWORD }}
      GPR_READ_TOKEN: ${{ secrets.GPR_READ_TOKEN }}
      GHA_TOKEN: ${{ secrets.GHA_TOKEN }}
```
