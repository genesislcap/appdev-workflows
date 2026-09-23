import argparse
import datetime
import re
import sys

import boto3
from botocore.exceptions import ClientError

rds = boto3.client("rds")

# Every clone must carry this prefix. It is the one thing standing between a
# bug in this script and an accidental delete/modify of a real prod or UAT
# cluster: the IAM policy for this role restricts DeleteDBCluster,
# DeleteDBInstance and ModifyDBCluster to resources matching this pattern, so
# even a wrong identifier here fails closed at the AWS layer, not just here.
CLONE_PREFIX_RE = re.compile(r"^clone-[a-z0-9-]+$")


def validate_new_cluster_id(new_cluster_id, source_cluster_id):
    if new_cluster_id == source_cluster_id:
        print("Error: new cluster identifier is the same as the source cluster - refusing to proceed.", file=sys.stderr)
        sys.exit(1)
    if not CLONE_PREFIX_RE.match(new_cluster_id):
        print(f"Error: new cluster identifier '{new_cluster_id}' must match {CLONE_PREFIX_RE.pattern} "
              f"(a mandatory 'clone-' prefix, lowercase letters/digits/hyphens only).", file=sys.stderr)
        sys.exit(1)


def wait_for_cluster_available(cluster_id):
    print(f"Waiting for cluster {cluster_id} to become available...")
    waiter = rds.get_waiter("db_cluster_available")
    waiter.wait(DBClusterIdentifier=cluster_id)
    print(f"Cluster {cluster_id} is now available.")


def wait_for_instance_available(instance_id):
    print(f"Waiting for instance {instance_id} to become available...")
    waiter = rds.get_waiter("db_instance_available")
    waiter.wait(DBInstanceIdentifier=instance_id)
    print(f"Instance {instance_id} is available.")


def cluster_exists(cluster_id):
    try:
        rds.describe_db_clusters(DBClusterIdentifier=cluster_id)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "DBClusterNotFoundFault":
            return False
        raise


def build_cluster_from_snapshot(new_cluster_id, snapshot_id, source_cluster_id,
                                 target_security_group_ids, target_subnet_group,
                                 target_kms_key_id=None, instance_class_override=None,
                                 min_acu_override=None, max_acu_override=None):
    """Restore a snapshot into a brand-new, permanent cluster identifier,
    landing it in the TARGET's own networking rather than the source's.

    Unlike appdev-workflows/restore-rds-snapshot (which restores into a
    temporary cluster and then swaps identifiers so the restore replaces the
    original), this never touches the source cluster's identifier at all -
    the new cluster keeps new_cluster_id permanently and the source is left
    running untouched throughout.
    """
    source_cluster = rds.describe_db_clusters(DBClusterIdentifier=source_cluster_id)["DBClusters"][0]

    print(f"Restoring snapshot {snapshot_id} into new cluster {new_cluster_id} ...")

    restore_args = {
        "DBClusterIdentifier": new_cluster_id,
        "SnapshotIdentifier": snapshot_id,
        "Engine": source_cluster["Engine"],
        "EngineVersion": source_cluster["EngineVersion"],
        # Target's own networking - deliberately NOT copied from source, so
        # the clone lands in the target environment's VPC/security posture.
        "VpcSecurityGroupIds": target_security_group_ids,
        "DBSubnetGroupName": target_subnet_group,
        "DBClusterParameterGroupName": source_cluster["DBClusterParameterGroup"],
    }

    if "Port" in source_cluster:
        restore_args["Port"] = source_cluster["Port"]
    if target_kms_key_id:
        restore_args["KmsKeyId"] = target_kms_key_id

    rds.restore_db_cluster_from_snapshot(**restore_args)
    wait_for_cluster_available(new_cluster_id)

    # --- Carry over non-networking operational settings from the source ---
    modify_args = {
        "DBClusterIdentifier": new_cluster_id,
        "ApplyImmediately": True,
    }

    if "BackupRetentionPeriod" in source_cluster:
        modify_args["BackupRetentionPeriod"] = source_cluster["BackupRetentionPeriod"]
    if "PreferredBackupWindow" in source_cluster:
        modify_args["PreferredBackupWindow"] = source_cluster["PreferredBackupWindow"]
    if "PreferredMaintenanceWindow" in source_cluster:
        modify_args["PreferredMaintenanceWindow"] = source_cluster["PreferredMaintenanceWindow"]
    if "EnabledCloudwatchLogsExports" in source_cluster:
        modify_args["CloudwatchLogsExportConfiguration"] = {
            "EnableLogTypes": source_cluster["EnabledCloudwatchLogsExports"]
        }
    if "ServerlessV2ScalingConfiguration" in source_cluster:
        modify_args["ServerlessV2ScalingConfiguration"] = source_cluster["ServerlessV2ScalingConfiguration"]

    # Override ACUs when asked - these clusters are all db.serverless, so
    # without this the clone silently inherits the source's (possibly prod)
    # capacity ceiling. instance_class_override is close to a no-op for them.
    if min_acu_override is not None or max_acu_override is not None:
        scaling = modify_args.get("ServerlessV2ScalingConfiguration", {})
        if min_acu_override is not None:
            scaling["MinCapacity"] = min_acu_override
        if max_acu_override is not None:
            scaling["MaxCapacity"] = max_acu_override
        modify_args["ServerlessV2ScalingConfiguration"] = scaling

    if len(modify_args) > 2:
        print(f"Applying source operational settings to {new_cluster_id} ...")
        rds.modify_db_cluster(**modify_args)
        wait_for_cluster_available(new_cluster_id)

    # --- Create one instance per source instance ---
    source_instances = [
        i for i in rds.describe_db_instances()["DBInstances"]
        if i.get("DBClusterIdentifier") == source_cluster_id
    ]

    wait_for_ids = []
    for index, inst in enumerate(source_instances, start=1):
        new_instance_id = f"{new_cluster_id}-{index}"
        print(f"Creating instance {new_instance_id} for {new_cluster_id}...")

        instance_args = {
            "DBInstanceIdentifier": new_instance_id,
            "DBClusterIdentifier": new_cluster_id,
            "Engine": inst["Engine"],
            "DBInstanceClass": instance_class_override or inst["DBInstanceClass"],
            "PubliclyAccessible": False,
            "AutoMinorVersionUpgrade": inst["AutoMinorVersionUpgrade"],
        }

        rds.create_db_instance(**instance_args)
        wait_for_ids.append(new_instance_id)

    for instance_id in wait_for_ids:
        wait_for_instance_available(instance_id)

    print(f"Cluster {new_cluster_id} and all instances are available.")


def rotate_master_credentials(cluster_id):
    """Move the cluster onto an AWS Secrets Manager-managed master password,
    then force a fresh random value - so nothing from the source snapshot's
    password carries over into the clone.

    Two modify_db_cluster calls are required: AWS does not allow
    ManageMasterUserPassword and RotateMasterUserPassword in the same call,
    and a restored cluster always starts with a self-managed (inherited)
    password, never an already-managed one.
    """
    print(f"Enabling Secrets Manager-managed master password for {cluster_id} ...")
    rds.modify_db_cluster(
        DBClusterIdentifier=cluster_id,
        ManageMasterUserPassword=True,
        ApplyImmediately=True,
    )
    wait_for_cluster_available(cluster_id)

    print(f"Rotating master password for {cluster_id} to a fresh value ...")
    rds.modify_db_cluster(
        DBClusterIdentifier=cluster_id,
        RotateMasterUserPassword=True,
        ApplyImmediately=True,
    )
    wait_for_cluster_available(cluster_id)

    cluster = rds.describe_db_clusters(DBClusterIdentifier=cluster_id)["DBClusters"][0]
    return cluster.get("MasterUserSecret", {}).get("SecretArn")


def tag_cluster(cluster_arn, owner, source_cluster_id, environment, expiry_date=None):
    tags = [
        {"Key": "Owner", "Value": owner},
        {"Key": "Source", "Value": source_cluster_id},
        {"Key": "Environment", "Value": environment},
        {"Key": "CreatedDate", "Value": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")},
        {"Key": "Clone", "Value": "true"},
    ]
    if expiry_date:
        tags.append({"Key": "Expiry", "Value": expiry_date})

    print(f"Tagging {cluster_arn} ...")
    rds.add_tags_to_resource(ResourceName=cluster_arn, Tags=tags)


def delete_cluster_and_instances(cluster_id):
    """Best-effort cleanup of a half-created clone after a failure."""
    # Second, independent guard against ever deleting anything that isn't a
    # clone we created - this function must never run against a real cluster.
    if not CLONE_PREFIX_RE.match(cluster_id):
        raise ValueError(f"Refusing to delete '{cluster_id}': does not match the clone naming pattern {CLONE_PREFIX_RE.pattern}.")

    print(f"Cleaning up failed clone {cluster_id} ...")

    instances = [
        i["DBInstanceIdentifier"]
        for i in rds.describe_db_instances()["DBInstances"]
        if i.get("DBClusterIdentifier") == cluster_id
    ]

    for inst_id in instances:
        print(f"Deleting instance {inst_id} ...")
        rds.delete_db_instance(DBInstanceIdentifier=inst_id, SkipFinalSnapshot=True)

    for inst_id in instances:
        rds.get_waiter("db_instance_deleted").wait(DBInstanceIdentifier=inst_id)

    print(f"Deleting cluster {cluster_id} ...")
    rds.delete_db_cluster(DBClusterIdentifier=cluster_id, SkipFinalSnapshot=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Clone an RDS cluster from a snapshot into a new, permanent identifier.")
    parser.add_argument("source_cluster_id")
    parser.add_argument("snapshot_id")
    parser.add_argument("new_cluster_id")
    parser.add_argument("target_security_group_ids", help="comma-separated list of VPC security group IDs")
    parser.add_argument("target_subnet_group")
    parser.add_argument("owner")
    parser.add_argument("environment")
    parser.add_argument("--kms-key-id", default=None)
    parser.add_argument("--instance-class", default=None)
    parser.add_argument("--min-acu", type=float, default=None)
    parser.add_argument("--max-acu", type=float, default=None)
    parser.add_argument("--expiry-date", default=None)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    security_group_ids = [sg.strip() for sg in args.target_security_group_ids.split(",") if sg.strip()]

    validate_new_cluster_id(args.new_cluster_id, args.source_cluster_id)

    if cluster_exists(args.new_cluster_id):
        print(f"Error: target cluster {args.new_cluster_id} already exists.", file=sys.stderr)
        sys.exit(1)

    try:
        build_cluster_from_snapshot(
            args.new_cluster_id,
            args.snapshot_id,
            args.source_cluster_id,
            security_group_ids,
            args.target_subnet_group,
            target_kms_key_id=args.kms_key_id,
            instance_class_override=args.instance_class,
            min_acu_override=args.min_acu,
            max_acu_override=args.max_acu,
        )
        secret_arn = rotate_master_credentials(args.new_cluster_id)

        cluster = rds.describe_db_clusters(DBClusterIdentifier=args.new_cluster_id)["DBClusters"][0]
        tag_cluster(cluster["DBClusterArn"], args.owner, args.source_cluster_id, args.environment, args.expiry_date)

        print(f"CLONE_CLUSTER_ID={args.new_cluster_id}")
        print(f"CLONE_ENDPOINT={cluster['Endpoint']}")
        print(f"CLONE_SECRET_ARN={secret_arn}")
    except Exception:
        print(f"Clone failed - cleaning up {args.new_cluster_id} to avoid leaving a half-created resource.", file=sys.stderr)
        if cluster_exists(args.new_cluster_id):
            delete_cluster_and_instances(args.new_cluster_id)
        raise
