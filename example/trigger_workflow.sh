# Script for triggering a GitHub workflow
#
# Required environment variables:
#
# GITHUB_TOKEN: contains a valid GitHub auth token
# GITHUB_ORG_REPO: a string with GitHub organization and repo names separated
#   by a /
# WORKFLOW_EVENT: name of github workflow event to trigger
#
# The workflow must have a repository_dispatch trigger. For example:
#
# on:
#   repository_dispatch:
#     types: debug
#
# In this case WORKFLOW_EVENT=debug
curl -H "Accept: application/vnd.github.everest-preview+json" \
    -H "Authorization: token $GITHUB_TOKEN" \
    --request POST \
    --data '{"event_type": "'"${WORKFLOW_EVENT}"'"}' \
        "https://api.github.com/repos/${GITHUB_ORG_REPO}/dispatches"
