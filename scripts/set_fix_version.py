import csv
import os
from jira import JIRA

# Retrieve environment variables
jira_url = os.environ['JIRA_URL']
JIRA_API_TOKEN = os.environ['JIRA_API_TOKEN']
jira_user = os.environ['JIRA_USER']
cur_tag = os.environ['CUR_TAG']
csv_file = os.environ['CSV_FILE']
dry_run = os.environ['DRY_RUN'].lower() == 'yes'

# Log Jira connectivity
print(f"Connecting to Jira at {jira_url} as {jira_user}")

# Connect to Jira
try:
    jira = JIRA(server=jira_url, basic_auth=(jira_user, JIRA_API_TOKEN))
    print("Successfully connected to Jira.")
except Exception as e:
    print(f"Failed to connect to Jira: {e}")
    exit(1)

# Continue with your logic
print(f"Reading CSV file: {csv_file}")
rows = []
with open(csv_file, newline='') as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append(row)

for row in rows:
    issue_id = row['JiraID']
    try:
        print(f"Processing Jira issue: {issue_id}")
        issue = jira.issue(issue_id)
        project_key = issue.fields.project.key
        current_fixversions = [v.name for v in getattr(issue.fields, 'fixVersions', [])]

        # Update logic here

        row['Status'] = issue.fields.status.name
        row['Summary'] = issue.fields.summary
    except Exception as e:
        print(f"Failed to process issue {issue_id}: {e}")
        row['Status'] = f"Failed: {e}"

# Write the results to the CSV file
with open(csv_file, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['JiraID', 'Project', 'Summary', 'Status', 'FixVersions'])
    writer.writeheader()
    writer.writerows(rows)

print("Fix Versions successfully updated.")
