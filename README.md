# Grafana Error Agent

Automated Grafana runtime-error triage for GitHub repositories.

This project provides a GitHub Actions agent that runs on a schedule, fetches recent Grafana errors through a configured Grafana MCP server, creates a GitHub issue with the findings, and assigns the issue to the GitHub Copilot coding agent so Copilot can raise a fix PR.

## What It Does

1. Runs on `workflow_dispatch` or a scheduled interval.
2. Connects to the Grafana MCP endpoint.
3. Discovers or uses a configured Grafana logs/errors tool.
4. Normalizes Grafana/Loki/alert-like responses into runtime findings.
5. Creates a GitHub issue with the relevant messages, labels, service name, timestamps, and Grafana links.
6. Assigns `copilot-swe-agent[bot]` through GitHub's issue assignment API.
7. Copilot opens a PR for human review.

## Quick Start

Add this workflow to the repository you want to monitor:

```yaml
name: Grafana Auto Fix

on:
  workflow_dispatch:
  schedule:
    - cron: '0 */6 * * *'

permissions:
  contents: read
  issues: write
  pull-requests: write

jobs:
  grafana-auto-fix:
    uses: Maersk-Global/grafana-error-agent/.github/workflows/reusable-grafana-fix.yml@main
    secrets:
      GITHUB_PAT: ${{ secrets.GIT_PAT }}
      GRAFANA_MCP_AUTH_CLIENT_ID: ${{ secrets.GRAFANA_MCP_AUTH_CLIENT_ID }}
      GRAFANA_MCP_AUTH_CLIENT_SECRET: ${{ secrets.GRAFANA_MCP_AUTH_CLIENT_SECRET }}
    with:
      service_name: ${{ github.event.repository.name }}
      grafana_dashboard_uid: ${{ vars.GRAFANA_DASHBOARD_UID }}
      grafana_datasource_uid: ${{ vars.GRAFANA_DATASOURCE_UID }}
      grafana_mcp_auth_token_url: ${{ vars.GRAFANA_MCP_AUTH_TOKEN_URL }}
      grafana_mcp_auth_scope: ${{ vars.GRAFANA_MCP_AUTH_SCOPE }}
      lookback_hours: 6
      max_errors: 10
      create_issue: true
```

## Reusable Workflow Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `grafana_mcp_server_url` | `https://grafana-mcp.westeurope.azure.mop.maersk.io/mcp` | Grafana MCP endpoint |
| `grafana_mcp_transport` | `auto` | `auto`, `streamable_http`, or `sse` |
| `grafana_mcp_tool` | auto-discover | Exact MCP tool name if discovery is not enough |
| `grafana_mcp_arguments` | `{}` | JSON object merged into tool arguments |
| `grafana_dashboard_uid` | empty | Grafana dashboard UID. When set, the agent derives Loki datasource/query from dashboard panels |
| `grafana_datasource_uid` | empty | Grafana datasource UID used by Loki/Prometheus MCP tools |
| `grafana_mcp_auth_token_url` | empty | Token endpoint used to fetch a fresh bearer token at runtime |
| `grafana_mcp_auth_scope` | empty | Optional OAuth scope sent to the token endpoint |
| `grafana_mcp_auth_audience` | empty | Optional OAuth audience sent to the token endpoint |
| `grafana_mcp_auth_extra_params` | empty | Optional JSON object of extra form parameters sent to the token endpoint |
| `query` | error-oriented query | Query passed when the selected tool accepts a query-like field |
| `service_name` | repository name | Service/application to inspect |
| `repo_label` | `repository` | Repository label name used in logs |
| `lookback_hours` | `24` | Recent time window |
| `max_errors` | `10` | Max findings included in the Copilot issue |
| `include_log_patterns` | `true` | Fetch related Loki log patterns when supported by the MCP server |
| `severity_filter` | `error` | Minimum severity: `critical`, `error`, `warning`, `info`, or `low` |
| `create_issue` | `true` | Create and assign a Copilot issue |
| `comment_on_pr` | `true` | Comment findings on pull requests |
| `fail_on_errors` | `false` | Fail the workflow when errors are found |

## Required Secrets

| Secret | Required | Description |
|--------|----------|-------------|
| `GITHUB_PAT` | Recommended | Caller repository PAT used to check out this repo, create issues, and assign Copilot |
| `GRAFANA_MCP_AUTH_TOKEN` | Optional | Static bearer token for Grafana MCP. Use only when the token is long-lived enough for the workflow run |
| `GRAFANA_MCP_AUTH_CLIENT_ID` | If using dynamic auth | Client ID sent to `grafana_mcp_auth_token_url` |
| `GRAFANA_MCP_AUTH_CLIENT_SECRET` | If using dynamic auth | Client secret sent to `grafana_mcp_auth_token_url` |

For short-lived tokens, configure the token endpoint and client credentials instead of storing the bearer token itself:

```yaml
secrets:
  GRAFANA_MCP_AUTH_CLIENT_ID: ${{ secrets.GRAFANA_MCP_AUTH_CLIENT_ID }}
  GRAFANA_MCP_AUTH_CLIENT_SECRET: ${{ secrets.GRAFANA_MCP_AUTH_CLIENT_SECRET }}
with:
  grafana_mcp_auth_token_url: ${{ vars.GRAFANA_MCP_AUTH_TOKEN_URL }}
  grafana_mcp_auth_scope: ${{ vars.GRAFANA_MCP_AUTH_SCOPE }}
```

The agent posts `grant_type=client_credentials`, `client_id`, `client_secret`, and optional `scope`, `audience`, or `grafana_mcp_auth_extra_params` to the token URL. It reads `access_token` from the JSON response and sends it to Grafana MCP as `Authorization: Bearer <token>`.

## Local Development

```bash
poetry install
poetry run pytest
python agent/grafana_error_agent.py --help
```

For local MCP testing:

```bash
export GRAFANA_MCP_SERVER_URL="https://grafana-mcp.westeurope.azure.mop.maersk.io/mcp"
export GRAFANA_DASHBOARD_UID="cevjpz2k1yhvka"
export GRAFANA_DATASOURCE_UID="your-loki-datasource-uid"
export GRAFANA_SERVICE_NAME="my-service"
export GRAFANA_LOOKBACK_HOURS="6"
python agent/grafana_error_agent.py --output grafana_results.json
```

For local testing with dynamic auth:

```bash
export GRAFANA_MCP_AUTH_TOKEN_URL="https://login.example.com/oauth2/v2.0/token"
export GRAFANA_MCP_AUTH_CLIENT_ID="client-id"
export GRAFANA_MCP_AUTH_CLIENT_SECRET="client-secret"
export GRAFANA_MCP_AUTH_SCOPE="api://grafana-mcp/.default"
python agent/grafana_error_agent.py --output grafana_results.json
```

If your Grafana MCP server exposes a specific tool contract, configure it explicitly:

```bash
export GRAFANA_MCP_TOOL="query_loki_logs"
export GRAFANA_MCP_ARGUMENTS='{"datasource_uid":"loki-main"}'
```

## Project Structure

```text
grafana-error-agent/
├── agent/
│   └── grafana_error_agent.py
├── .github/workflows/
│   └── reusable-grafana-fix.yml
├── workflow-examples/
│   └── grafana-auto-fix.yml
├── tests/
│   └── test_grafana_error_agent.py
├── pyproject.toml
└── README.md
```
