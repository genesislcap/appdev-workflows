# clone-rds-snapshot

Clones an RDS/Aurora cluster into a **brand-new, permanent** cluster identifier. The source cluster is never renamed, modified, or replaced - it keeps running untouched, and the clone exists alongside it under its own name.

This is the sibling of [`restore-rds-snapshot`](../restore-rds-snapshot), not a variant of it: `restore-rds-snapshot` restores a snapshot *back into the same cluster identifier* (a rollback/refresh - it swaps the restored data in and the old data out). `clone-rds-snapshot` instead builds a second, independent cluster from the snapshot, so you end up with both the original and the clone.

## Self-service entry point

Most people should use the **Clone RDS Database** workflow (`.github/workflows/clone-rds.yml` in this repo) via `workflow_dispatch`, rather than calling this action directly. See its own inputs for the full form.

## Naming rule (required)

Every clone identifier **must start with `clone-`** (e.g. `clone-uat-2`, `clone-bmoeu-temp-oct`). This is enforced twice - once in `action.yaml` before any AWS call, and again in `clone-db.py` - and it is the one thing standing between a bug in this action and an accidental delete/modify of a real prod or UAT cluster: in any account where this is deployed, the IAM role's permissions policy should restrict `DeleteDBCluster`/`DeleteDBInstance`/`ModifyDBCluster` to resources matching `clone-*`, so a wrong identifier fails closed at the AWS layer too, not just in this code. See **Required IAM permissions** below for what is and isn't reliably enforceable this way.

## What it does

1. Finds the source cluster by tags (`AppName` / `Customer` / `EnvironmentName`) - same discovery mechanism as `rds-snapshot` / `restore-rds-snapshot`. If more than one live cluster matches the given tags, this fails loudly rather than guessing - a real risk in a shared, multi-client account where tags could overlap. The resolved source cluster ID is written to the job summary before anything else happens, so it can be checked before the restore step runs.
2. Checks the requested new identifier matches the naming rule above and doesn't already exist - fails fast before touching AWS otherwise.
3. Takes a fresh manual snapshot of the source (unless a `snapshot-id` was given). This is a control-plane operation against the source cluster - it does not read or write application data, so it does not count as a write to the source database.
4. Restores that snapshot into the new identifier, using the **target's own** VPC security groups and subnet group (not the source's) - this is the key acceptance criterion: the clone must land in the target environment's networking.
5. Carries over the source's non-networking operational settings (backup window, maintenance window, CloudWatch log exports, Serverless v2 scaling) and creates matching instances (instance class and, separately, Serverless v2 min/max ACU are both overridable - these clusters are all `db.serverless`, so ACU is what actually controls capacity/cost, not instance class).
6. Moves the clone onto an AWS Secrets Manager-managed master password and forces a fresh rotation, so the source's password never carries over into the clone.
7. Tags the clone with `Owner`, `Source`, `Environment`, `CreatedDate`, `Clone=true`, and an optional `Expiry` date.
8. On any failure, deletes whatever was half-created (instances then cluster) before failing the job, so a failed run doesn't leave orphaned billed resources - the same failure mode `restore-rds-snapshot`'s own docs call out as a manual cleanup chore today.

## Inputs

See `action.yaml` for the full, authoritative list. Key ones:

| Input | Required | Description |
|---|---|---|
| `source-environment` / `source-app-name` / `source-customer` | yes | Tags identifying the source cluster |
| `new-cluster-identifier` | yes | The clone's permanent identifier - **must start with `clone-`** |
| `target-security-group-ids` / `target-subnet-group` | yes | The **target** environment's own networking |
| `environment` | yes | `EnvironmentName` tag applied to the clone |
| `owner` | yes | Tagged onto the clone for ownership/cleanup tracking |
| `snapshot-id` | no | Defaults to a fresh snapshot of the source |
| `instance-class` | no | Defaults to the source's instance class |
| `target-min-acu` / `target-max-acu` | no | Aurora Serverless v2 capacity override; defaults to the source's scaling config |
| `target-kms-key-id` | no | Re-encrypt with a different key; defaults to the snapshot's own |
| `expiry-date` | no | TTL tag for a later cleanup job to find (no automatic deletion - out of scope for now) |

## Deleting a clone

There is no automatic expiry yet (tracked as a follow-up). To delete a clone by hand once it's no longer needed:

```bash
aws rds delete-db-instance --db-instance-identifier <clone-id>-1 --skip-final-snapshot
# repeat for every instance the clone created
aws rds delete-db-cluster --db-cluster-identifier <clone-id> --skip-final-snapshot
```

Its Secrets Manager secret (see the `secret-arn` output / job summary) is not deleted automatically either - remove it separately if you want it gone immediately rather than waiting out its recovery window.

## Important notes & caveats

- **Credential rotation needs two `modify_db_cluster` calls.** AWS does not accept `ManageMasterUserPassword` and `RotateMasterUserPassword` in the same call, and a freshly-restored cluster always starts with a self-managed (inherited) password. This has not yet been exercised against a real snapshot restore in this org - verify it behaves as expected in a non-prod dry run before relying on it for a prod-sourced clone.
- **Cross-KMS-key restore is unverified.** `target-kms-key-id` is passed straight through to `restore_db_cluster_from_snapshot`; AWS documents this as supported, but it hasn't been tested here against a snapshot encrypted with a different account/region's key.
- **The script is checked out from `develop` at runtime**, like `restore-rds-snapshot` - clone behavior always follows the latest `develop`, regardless of which ref your workflow pins the action to.
- **Duration:** expect this to take a similar order of magnitude to `restore-rds-snapshot` (30+ minutes for a typical cluster) - snapshot creation, restore, instance creation, and two credential-rotation modify cycles all involve waiting for RDS state transitions.
- **Scope:** this only covers Aurora clusters (matches this org's existing `rds-aurora-postgres` Terraform module and the existing snapshot/restore actions). Aurora fast cloning (copy-on-write, same-account/region, no snapshot step) is a documented follow-up, not implemented here.
- **Front-door setup dependency:** `clone-rds.yml` selects a GitHub Environment named `<customer>-<source-environment>` to source its AWS credentials and to gate prod sources behind required reviewers. Creating those environments (and their `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` secrets, and reviewers on the prod ones) is a one-time setup step per customer, not something this action or workflow creates for you.

## Required IAM permissions

In addition to what `rds-snapshot` already needs:

- `rds:RestoreDBClusterFromSnapshot`, `rds:CreateDBInstance` - creates the clone
- `rds:ModifyDBCluster` - **restrict to `arn:aws:rds:*:<account>:cluster:clone-*`**
- `rds:DeleteDBInstance`, `rds:DeleteDBCluster` (failure-path cleanup only) - **restrict to `arn:aws:rds:*:<account>:cluster:clone-*` / `:db:clone-*`**
- `rds:AddTagsToResource`
- `kms:CreateGrant`, `kms:DescribeKey` via `rds.amazonaws.com` (only if using `target-kms-key-id`)

**What resource-level scoping can and can't do here - read before writing the policy.** AWS evaluates resource-level IAM conditions against a resource that already exists at authorization time.

- `ModifyDBCluster`, `DeleteDBCluster`, `DeleteDBInstance` act on the clone, which already exists when these are called - restricting them to the `clone-*` ARN pattern is reliable, and it's the enforcement that actually matters: it's what stands between a bug in this script and a real prod/UAT cluster being modified or deleted, independent of the application-level checks in `action.yaml`/`clone-db.py`.
- `RestoreDBClusterFromSnapshot` and `CreateDBInstance` act on a resource that does **not exist yet** - there is nothing for a resource-ARN or tag condition to match against at authorization time, so **do not rely on IAM to enforce the `clone-` prefix on these two actions.** That enforcement is application-level only (this action refuses to proceed with a non-`clone-` identifier), backed by the fact that only this one workflow/role can call it at all.
- `CreateDBClusterSnapshot` (used to snapshot the *source*) acts on a resource that already exists and is tagged, so it can be reliably restricted via `Condition: aws:ResourceTag/Customer` etc. to the intended client(s).

**Verify the `clone-*` restriction on `ModifyDBCluster`/`Delete*` actually denies a call against a real cluster name in a sandbox before trusting this policy against a production account.** This is the one check that validates or invalidates the whole safety design.

The `kms:CreateGrant`/`DescribeKey` `ViaService` pattern here follows the one already documented for the RPM/Jenkins RDS restore pipeline - that policy was written for a static IAM user, not an OIDC role, so treat it as a starting point to verify, not a pre-validated fit.

## Rollout sequence for a new account (read before pointing this at production)

1. Prove the mechanics somewhere nothing client-facing can be affected first - a disposable cluster in a non-production account. Deliberately trigger the failure-cleanup path too (e.g. an invalid target subnet group) and confirm no orphaned resources are left behind.
2. Only then set up the IAM role/trust policy in the target account, scoped as above, reviewed by whoever administers that account.
3. First real run against a shared/production account should target a non-prod source cluster, watched live.
4. Only clone from a prod source once step 3 has succeeded and the GitHub Environment for that `<customer>-prod` combination has required reviewers actually configured - `clone-rds.yml`'s approval gate does nothing until that environment exists.
