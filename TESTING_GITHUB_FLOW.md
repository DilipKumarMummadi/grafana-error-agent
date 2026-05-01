# Testing PR Creation Flow Locally

This guide shows you how to test the entire Grafana Error Agent workflow locally, including GitHub issue creation and Copilot assignment.

## Prerequisites

1. **GitHub CLI (`gh`)** installed: https://cli.github.com/
2. **Authenticated with GitHub**: `gh auth login`
3. **Repository access**: Write permissions to create issues
4. **Agent configured**: `.env` file with Grafana credentials
5. **Test repository**: Use a test/dev repo to avoid polluting production

## Step-by-Step Testing

### 1️⃣ Run the Agent Locally

This generates the `grafana_results.json` file with errors and GitHub issue payload:

```bash
export $(cat .env | xargs)
python -m agent.grafana_error_agent --output grafana_results.json
```

**Expected output:**
```json
{
  "success": true,
  "errors_found": 10,
  "github_issue": {
    "title": "[Grafana Agent] Fix 10 runtime error(s)",
    "body": "## Grafana Runtime Errors Detected\n...",
    "labels": ["grafana", "runtime-error", "automated", "copilot"]
  }
}
```

---

### 2️⃣ Verify the Generated Issue Payload

Check what issue will be created:

```bash
# View the issue title and body
jq '.github_issue' grafana_results.json | head -50

# Count errors
jq '.errors_found' grafana_results.json

# View labels
jq '.github_issue.labels[]' grafana_results.json
```

---

### 3️⃣ Test GitHub Authentication

Ensure `gh` can access your repository:

```bash
gh auth status
gh repo view  # Should show your current repo
```

---

### 4️⃣ Create a Test Issue (Optional)

Test manual issue creation before automating:

```bash
# Extract issue details
TITLE=$(jq -r '.github_issue.title' grafana_results.json)
jq -r '.github_issue.body' grafana_results.json > /tmp/issue_body.md

# Create issue
gh issue create \
  --title "$TITLE" \
  --body-file /tmp/issue_body.md \
  --label "grafana" \
  --label "test"
```

---

### 5️⃣ Use the Automated Test Script

Run the provided test script to simulate the full workflow:

```bash
chmod +x local_github_test.sh
./local_github_test.sh
```

This script will:
- ✅ Check GitHub CLI is installed and authenticated
- ✅ Validate `grafana_results.json` exists
- ✅ Ask for confirmation before creating a real issue
- ✅ Create the GitHub issue with labels
- ✅ Assign it to `copilot-swe-agent[bot]`
- ✅ Show the issue URL

---

## Testing Different Scenarios

### Scenario 1: Dry Run (No Issue Creation)

Just generate the payload without creating an issue:

```bash
python -m agent.grafana_error_agent --output grafana_results.json
jq '.github_issue' grafana_results.json
# Review, then decide if you want to create it
```

### Scenario 2: Test on Different Repository

```bash
# Clone or switch to a test repo
cd /path/to/test-repo

# Run agent with specific repo context
export GITHUB_OWNER=your-org
export GITHUB_REPO=test-repo
export $(cat /path/to/grafana-error-agent/.env | xargs)

python -m /path/to/grafana-error-agent/agent/grafana_error_agent.py --output results.json

# Create issue in test repo
cd /path/to/test-repo
bash /path/to/grafana-error-agent/local_github_test.sh
```

### Scenario 3: Verify Copilot Assignment

After creating an issue, verify Copilot was assigned:

```bash
ISSUE_NUMBER=123  # Replace with your issue number
gh issue view $ISSUE_NUMBER --json assignees --jq '.assignees[].login'

# Should output:
# copilot-swe-agent[bot]
```

### Scenario 4: Check PR Creation Status

Once Copilot is assigned, it will automatically create a PR:

```bash
# List pull requests for this repo
gh pr list --state open

# Find the PR created by copilot-swe-agent[bot]
gh pr list --author copilot-swe-agent[bot]

# View the PR details
gh pr view <PR_NUMBER>
```

---

## Simulating the Full Workflow

Here's a complete workflow simulation:

```bash
#!/bin/bash
# Full workflow test script

set -e

echo "1️⃣  Running Grafana Error Agent..."
export $(cat .env | xargs)
python -m agent.grafana_error_agent --output grafana_results.json

echo ""
echo "2️⃣  Checking results..."
ERRORS=$(jq '.errors_found' grafana_results.json)
echo "   Errors found: $ERRORS"

if [ "$ERRORS" == "0" ]; then
    echo "   ⚠️  No errors found, skipping issue creation"
    exit 0
fi

echo ""
echo "3️⃣  Creating GitHub issue..."
./local_github_test.sh

echo ""
echo "4️⃣  Waiting for Copilot to process..."
echo "   (Check your repository's PR page in 1-2 minutes)"
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **`gh: command not found`** | Install GitHub CLI: https://cli.github.com/ |
| **`gh: not authenticated`** | Run `gh auth login` and follow prompts |
| **`grafana_results.json` not found** | Run agent first: `python -m agent.grafana_error_agent --output grafana_results.json` |
| **Issue created but Copilot not assigned** | Check repo settings for Copilot permissions; may need admin approval |
| **HTTP 403 when creating issue** | Verify PAT has `issues:write` permission; regenerate if needed |
| **Copilot assignment fails** | May need to use GitHub PAT instead of `github.token` |

---

## Differences: Local vs GitHub Actions

| Aspect | Local | GitHub Actions |
|--------|-------|-----------------|
| **Grafana Query** | Uses your `.env` config | Uses workflow secrets/inputs |
| **GitHub Auth** | Uses your personal `gh` auth | Uses GITHUB_PAT secret or github.token |
| **Issue Creation** | Manual or via script | Automatic if `create_issue: true` |
| **Copilot Assignment** | Manual or via script | Automatic via API |
| **Scheduling** | One-time or manual | Scheduled (e.g., every 6 hours) |
| **Repository Context** | Current directory | Target repository from workflow |

---

## Next Steps

After successful local testing:

1. ✅ Verify issue was created with all error details
2. ✅ Confirm Copilot was assigned (`copilot-swe-agent[bot]`)
3. ✅ Check that Copilot creates a PR with fixes
4. ✅ Review the PR for accuracy and completeness
5. ✅ If satisfied, add workflow to your production repository
6. ✅ Configure GitHub Actions to run on schedule

---

## Monitoring PR Creation

Once Copilot is assigned to an issue:

```bash
# Watch for PR creation (check every 30 seconds for 5 minutes)
for i in {1..10}; do
  echo "Checking for PR (attempt $i)..."
  gh pr list --author copilot-swe-agent[bot] --json number,title,url
  if [ $i -lt 10 ]; then sleep 30; fi
done
```

---

## Common Commands Reference

```bash
# Run full workflow
export $(cat .env | xargs)
python -m agent.grafana_error_agent --output grafana_results.json

# View results summary
jq '{errors_found: .errors_found, tool: .tool, errors: (.errors | length)}' grafana_results.json

# Create test issue
./local_github_test.sh

# List all open issues
gh issue list

# View issue by number
gh issue view 123 --json title,body,assignees

# List all PRs from Copilot
gh pr list --author copilot-swe-agent[bot]

# Check PR status
gh pr view <PR_NUMBER>

# View PR comments
gh pr view <PR_NUMBER> --json comments
```

---

## Tips for Successful Testing

1. **Start with test repository**: Avoid polluting production with test issues
2. **Check permissions**: Ensure GITHUB_PAT has write access
3. **Verify Grafana access**: Test connection before running full workflow
4. **Review issue body**: Check that errors are properly formatted
5. **Monitor Copilot**: Verify bot assignment in issue page
6. **Set expectations**: Copilot may take 1-2 minutes to create PR
7. **Keep test issues**: Track what works for future reference

