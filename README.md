# appdev-workflows
genesis cicd workflow files

## SendIt CLI (S3 + repository dispatch)

See [scripts/sendit_upload_and_dispatch/README.md](scripts/sendit_upload_and_dispatch/README.md) for:

- Uploading CSVs to the tagged data bucket
- Triggering [`.github/workflows/sendit-repository-dispatch.yml`](.github/workflows/sendit-repository-dispatch.yml)
- Building distributable CLI binaries via [`.github/workflows/build-sendit-cli.yml`](.github/workflows/build-sendit-cli.yml)
