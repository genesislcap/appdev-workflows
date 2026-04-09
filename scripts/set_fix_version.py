import csv
import os
from jira import JIRA

# -------------------------
# Retrieve environment variables
# -------------------------
jira_url = os.environ['JIRA_URL']
jira_user = os.environ['JIRA_USER']
jira_api_token = os.environ['JIRA_API_TOKEN']
cur_tag = os.environ['CUR_TAG']
fixversion_prefix = os.environ.get('FIXVERSION_PREFIX', '')  # optional, defaults to empty string
fixversion = f"{fixversion_prefix}{cur_tag}"  # combined value used everywhere
csv_file = os.environ['CSV_FILE']
dry_run = os.environ['DRY_RUN'].lower() == 'yes'

# -------------------------
# Log final fixversion to be used
# -------------------------
print(f"FixVersion to be applied: '{fixversion}'")
if dry_run:
    print("Dry run mode: Jira issues will NOT be updated.")

# -------------------------
# Connect to Jira
# -------------------------
print(f"Connecting to Jira at {jira_url} as {jira_user}")
try:
    jira = JIRA(server=jira_url, basic_auth=(jira_user, jira_api_token))
    print("Successfully connected to Jira.")
except Exception as e:
    print(f"Failed to connect to Jira: {e}")
    exit(1)

# -------------------------
# Read the CSV and track Jira IDs to avoid duplicates
# -------------------------
rows = []
seen_jiras = set()
with open(csv_file, newline='') as f:
    reader = csv.DictReader(f)
    for row in reader:
        commit_jira_id = row.get('CommitJiraID') or row.get('JiraID')
        if commit_jira_id and commit_jira_id not in seen_jiras:
            seen_jiras.add(commit_jira_id)
            rows.append({'CommitJiraID': commit_jira_id})

# -------------------------
# Process each Jira issue
# -------------------------
for row in rows:
    commit_jira_id = row['CommitJiraID']
    try:
        print(f"Processing Jira issue from commit: {commit_jira_id}")
        issue = jira.issue(commit_jira_id)

        # Current Jira key (may have changed if issue moved)
        current_key = issue.key
        row['NewIssueKey'] = current_key if current_key != commit_jira_id else ""

        # Populate Jira info
        row['Project'] = issue.fields.project.key
        row['Summary'] = issue.fields.summary
        row['Status'] = issue.fields.status.name

        # Current FixVersions
        current_fixversions = [v.name for v in getattr(issue.fields, 'fixVersions', [])]

        # Get the project key
        project_key = issue.fields.project.key
        
        # Check if version exists in Jira
        versions = jira.project_versions(project_key)
        version_names = [v.name for v in versions]
        
        if fixversion not in version_names and not dry_run:
            # Create the version in Jira
            print(f"Creating fixVersion '{fixversion}' in project {project_key}")
            jira.create_version(name=fixversion, project=project_key)

        # Convert current fixversions to list of objects
        fix_versions_payload = [{"name": v} for v in current_fixversions]

        # Add fixversion if not already present
        if not dry_run and fixversion not in current_fixversions:
            fix_versions_payload.append({"name": fixversion})
            issue.update(fields={"fixVersions": fix_versions_payload})
        
        # Update CSV column
        row['FixVersions'] = ", ".join([v['name'] for v in fix_versions_payload])

    except Exception as e:
        print(f"Failed to process issue {commit_jira_id}: {e}")
        row['Status'] = f"Failed: {e}"
        row['Project'] = ""
        row['FixVersions'] = ""
        row['NewIssueKey'] = ""

# -------------------------
# Write enriched CSV back
# -------------------------
fieldnames = ['CommitJiraID', 'NewIssueKey', 'Project', 'Summary', 'Status', 'FixVersions']
with open(csv_file, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"CSV file updated: {csv_file}")
if dry_run:
    print("Dry run mode: Jira issues were NOT updated.")
else:
    print("Fix Versions successfully updated in Jira.")
