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
csv_file = os.environ['CSV_FILE']
dry_run = os.environ['DRY_RUN'].lower() == 'yes'

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
        if commit_jira_id not in seen_jiras:
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

        # Add cur_tag to Jira only if not dry-run
        if not dry_run and cur_tag not in current_fixversions:
            issue.update(fields={"fixVersions": current_fixversions + [cur_tag]})
            current_fixversions.append(cur_tag)

        # Populate FixVersions column in CSV
        row['FixVersions'] = ", ".join(current_fixversions)

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
