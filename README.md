# appdev-workflows
genesis cicd workflow files

## SendIt workflow orchestration

This repository owns the SendIt dispatch workflow:

- [`.github/workflows/sendit-workflow-dispatch.yml`](.github/workflows/sendit-workflow-dispatch.yml)

The workflow is triggered via `workflow_dispatch` and is intended to be called by the standalone SendIt CLI. Because the CLI can pass a specific git ref and the workflow uses the checked-out local `./sendit` action, workflow and action changes can be tested together from a feature branch before merging.

The SendIt CLI source and binary build workflow now live in `genesislcap/devops-tools` under `sendit_upload_and_dispatch`.
