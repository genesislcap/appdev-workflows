import boto3
import sys
import time
import datetime

rds = boto3.client("rds")

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

def wait_for_cluster_modifiable(cluster_id, interval=15, timeout=1800):
    """Wait until the cluster is in a modifiable state (available, not configuring)."""
    print(f"Waiting for cluster {cluster_id} to become fully modifiable...")
    elapsed = 0
    while elapsed < timeout:
        cluster = rds.describe_db_clusters(DBClusterIdentifier=cluster_id)["DBClusters"][0]
        status = cluster["Status"]
        if status == "available":
            print(f"Cluster {cluster_id} is now modifiable.")
            return
        print(f"Current cluster status: {status}, waiting {interval}s...")
        time.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Cluster {cluster_id} not modifiable after {timeout} seconds")


def create_temp_cluster_from_snapshot(temp_name, snapshot_id, source_cluster_id):
    """Restore snapshot into a temporary cluster and configure it like the source."""
    source_cluster = rds.describe_db_clusters(DBClusterIdentifier=source_cluster_id)["DBClusters"][0]

    print(f"Restoring snapshot {snapshot_id} into temporary cluster {temp_name} ...")

    restore_args = {
        "DBClusterIdentifier": temp_name,
        "SnapshotIdentifier": snapshot_id,
        "Engine": source_cluster["Engine"],
        "EngineVersion": source_cluster["EngineVersion"],
        "VpcSecurityGroupIds": [sg["VpcSecurityGroupId"] for sg in source_cluster["VpcSecurityGroups"]],
        "DBSubnetGroupName": source_cluster["DBSubnetGroup"],
        "DBClusterParameterGroupName": source_cluster["DBClusterParameterGroup"],
        "Tags": source_cluster.get("TagList", []),
    }

    if "Port" in source_cluster:
        restore_args["Port"] = source_cluster["Port"]

    # Create cluster from snapshot
    rds.restore_db_cluster_from_snapshot(**restore_args)
    wait_for_cluster_available(temp_name)

    # --- Apply configuration settings to match source ---
    modify_args = {
        "DBClusterIdentifier": temp_name,
        "ApplyImmediately": True,
    }

    # Backup and maintenance settings
    if "BackupRetentionPeriod" in source_cluster:
        modify_args["BackupRetentionPeriod"] = source_cluster["BackupRetentionPeriod"]
    if "PreferredBackupWindow" in source_cluster:
        modify_args["PreferredBackupWindow"] = source_cluster["PreferredBackupWindow"]
    if "PreferredMaintenanceWindow" in source_cluster:
        modify_args["PreferredMaintenanceWindow"] = source_cluster["PreferredMaintenanceWindow"]

    # Performance Insights & monitoring roles
    if "EnabledCloudwatchLogsExports" in source_cluster:
        modify_args["CloudwatchLogsExportConfiguration"] = {
            "EnableLogTypes": source_cluster["EnabledCloudwatchLogsExports"]
        }
    if "MonitoringRoleArn" in source_cluster:
        modify_args["MonitoringRoleArn"] = source_cluster["MonitoringRoleArn"]
    if "MonitoringInterval" in source_cluster:
        modify_args["MonitoringInterval"] = source_cluster["MonitoringInterval"]
    if "PerformanceInsightsEnabled" in source_cluster and source_cluster["PerformanceInsightsEnabled"]:
        modify_args["PerformanceInsightsEnabled"] = True
        if "PerformanceInsightsKMSKeyId" in source_cluster:
            modify_args["PerformanceInsightsKMSKeyId"] = source_cluster["PerformanceInsightsKMSKeyId"]
        if "PerformanceInsightsRetentionPeriod" in source_cluster:
            modify_args["PerformanceInsightsRetentionPeriod"] = source_cluster["PerformanceInsightsRetentionPeriod"]

    # Serverless v2 scaling
    if "ServerlessV2ScalingConfiguration" in source_cluster:
        modify_args["ServerlessV2ScalingConfiguration"] = source_cluster["ServerlessV2ScalingConfiguration"]

    print(f"Applying source configuration to cluster {temp_name} ...")
    rds.modify_db_cluster(**modify_args)
    wait_for_cluster_available(temp_name)
    print(f"Configuration for {temp_name} now matches source cluster {source_cluster_id}.")

    # --- Create DB instances for new cluster ---
    source_instances = [
        i for i in rds.describe_db_instances()["DBInstances"]
        if i.get("DBClusterIdentifier") == source_cluster_id
    ]

    wait_for_ids = []
    for inst in source_instances:
        new_instance_id = f"{temp_name}-{inst['DBInstanceIdentifier'].split('-')[-1]}"
        print(f"Creating instance {new_instance_id} for {temp_name}...")

        instance_args = {
            "DBInstanceIdentifier": new_instance_id,
            "DBClusterIdentifier": temp_name,
            "Engine": inst["Engine"],
            "DBInstanceClass": inst["DBInstanceClass"],
            "PubliclyAccessible": inst["PubliclyAccessible"],
            "DBParameterGroupName": inst["DBParameterGroups"][0]["DBParameterGroupName"],
            "AutoMinorVersionUpgrade": inst["AutoMinorVersionUpgrade"],
            "PromotionTier": inst.get("PromotionTier", 1),
        }

        # Copy Enhanced Monitoring
        if "MonitoringRoleArn" in inst:
            instance_args["MonitoringRoleArn"] = inst["MonitoringRoleArn"]
        if "MonitoringInterval" in inst:
            instance_args["MonitoringInterval"] = inst["MonitoringInterval"]

        # Copy Performance Insights
        if "PerformanceInsightsEnabled" in inst and inst["PerformanceInsightsEnabled"]:
            instance_args["PerformanceInsightsEnabled"] = True
            if "PerformanceInsightsKMSKeyId" in inst:
                instance_args["PerformanceInsightsKMSKeyId"] = inst["PerformanceInsightsKMSKeyId"]
            if "PerformanceInsightsRetentionPeriod" in inst:
                instance_args["PerformanceInsightsRetentionPeriod"] = inst["PerformanceInsightsRetentionPeriod"]

        # Copy tags from source cluster (so cost/billing tracking remains consistent)
        tags = source_cluster.get("TagList", [])
        rds.create_db_instance(**instance_args, Tags=tags)
        wait_for_ids.append(new_instance_id)

    for id in wait_for_ids:
        wait_for_instance_available(id)

    print(f"Temporary cluster {temp_name} and all instances are fully available.")
    return temp_name

def rename_cluster(old_name, new_name):
    """Rename a cluster."""
    print(f"Renaming cluster {old_name} -> {new_name} ...")

    wait_for_cluster_modifiable(old_name)
    rds.modify_db_cluster(
        DBClusterIdentifier=old_name,
        NewDBClusterIdentifier=new_name,
        ApplyImmediately=True
    )
    time.sleep(60)
    wait_for_cluster_available(new_name)


def delete_cluster_and_instances(cluster_id):
    """Delete all instances in a cluster, then the cluster itself."""
    print(f"Deleting old cluster {cluster_id} and its instances...")

    instances = [
        i["DBInstanceIdentifier"]
        for i in rds.describe_db_instances()["DBInstances"]
        if i.get("DBClusterIdentifier") == cluster_id
    ]

    # Delete instances first
    for inst_id in instances:
        print(f"Deleting instance {inst_id} ...")
        rds.delete_db_instance(DBInstanceIdentifier=inst_id, SkipFinalSnapshot=True)
        print(f"Instance {inst_id} deletion initiated.")

    # Wait for all instances to be gone
    while True:
        existing = [
            i["DBInstanceIdentifier"]
            for i in rds.describe_db_instances()["DBInstances"]
            if i.get("DBClusterIdentifier") == cluster_id
        ]
        if not existing:
            break
        print(f"Waiting for instances {existing} to be deleted...")
        time.sleep(30)

    # Delete the cluster
    print(f"Deleting cluster {cluster_id} ...")
    rds.delete_db_cluster(DBClusterIdentifier=cluster_id, SkipFinalSnapshot=True)
    print(f"Cluster {cluster_id} deletion initiated.")


def swap_clusters(original_name, temp_name, delete_old=False):
    """Rename old cluster to timestamp, rename temp to original, optionally delete old."""
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
    renamed_old_name = f"{original_name}-{timestamp}"

    # Rename old cluster
    rename_cluster(original_name, renamed_old_name)

    # Rename temp cluster to original name
    rename_cluster(temp_name, original_name)
    print(f"Swap complete. New cluster is now {original_name}, old cluster is {renamed_old_name}.")

    if delete_old:
        delete_cluster_and_instances(renamed_old_name)
        print(f"Old cluster {renamed_old_name} deleted.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python clone_rds_swap.py <source-cluster-id> <snapshot-id> [--delete-old]")
        sys.exit(1)

    source_cluster_id = sys.argv[1]
    snapshot_id = sys.argv[2]
    delete_old = "--delete-old" in sys.argv

    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
    temp_cluster_id = f"{source_cluster_id}-temp-{timestamp}"

    create_temp_cluster_from_snapshot(temp_cluster_id, snapshot_id, source_cluster_id)
    swap_clusters(source_cluster_id, temp_cluster_id, delete_old=delete_old)
