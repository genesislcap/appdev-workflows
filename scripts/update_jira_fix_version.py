
from jira import JIRA, JIRAError
from argparse import ArgumentParser

parser = ArgumentParser(
    prog='update_jira_fix_version',
    description='Reads in a file containing a list of Jira tickets' +
                ' to update and tags all of them with a given fixVersion. ' +
                ' Used as part of the automated platform release process.'
)
parser.add_argument("-f", "--file", dest="filename",
                    help="file containing a list of Jira tickets to update", metavar="FILE", required=True)
parser.add_argument("-v", "--version", dest="version",
                    help="version to create in all projects and update on all tickets", metavar="VERSION",
                    required=True)
parser.add_argument("-u", "--user", dest="user",
                    help="jira user to use to perform updates", metavar="USER", required=True)
parser.add_argument("-k", "--key", dest="key",
                    help="jira API key for user authentication", metavar="KEY", required=True)
parser.add_argument("-s", "--server", dest="server",
                    help="jira server URL", metavar="SERVER", default='https://genesisglobal.atlassian.net')

args = parser.parse_args()
user = args.user
apikey = args.key
filename = args.filename
fixVersion = args.version

options = {
    'server': args.server
}

jira = JIRA(options=options, basic_auth=(user, apikey))
projects = set()

with open(filename, 'r') as file:
    tickets = [line.strip().replace('\n', '') for line in file.readlines()]

for ticket in tickets:
    projects.add(ticket.split('-')[0])

for project in projects:
    try:
        version = jira.create_version(name=fixVersion, project=project)
        print("Created version {} in project {}".format(version, project))
    except JIRAError as e:
        print("Caught exception while attempting to create version {} in project {}: {}"
              .format(fixVersion, project, e.response.text))

for ticket in tickets:
    try:
        print("Retrieving issue {}".format(ticket))
        issue = jira.issue(ticket)
        print("Retrieved issue {}".format(ticket))
        fixVersions = [{'name': version.name} for version in issue.get_field("fixVersions")]
        fixVersions.append({'name': fixVersion})
        print("Updating issue {} with fix version {}".format(ticket, fixVersion))
        issue.update(fields={'fixVersions': fixVersions})
        print("Successfully updated issue {} with fix version {}".format(ticket, fixVersion))
    except JIRAError as e:
        print("Caught exception while attempting to update fix version on ticket {} : {}"
              .format(ticket, e.response.text))
