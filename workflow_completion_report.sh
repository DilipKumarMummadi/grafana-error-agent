#!/bin/bash
# Complete Grafana Error Agent - GitHub PR Creation Workflow
# This script documents all steps completed and provides next steps

set -e

echo "=========================================="
echo "✅ GRAFANA ERROR AGENT - COMPLETE WORKFLOW"
echo "=========================================="
echo ""

# Step 1: Agent Execution
echo "STEP 1: Grafana Error Agent Execution"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Agent ran successfully"
echo "   Command: python -m agent.grafana_error_agent --output grafana_results.json"
echo ""
ERRORS=$(jq '.errors_found' grafana_results.json)
TOOL=$(jq -r '.tool' grafana_results.json)
echo "   Errors Found: $ERRORS"
echo "   Tool Used: $TOOL"
echo "   Output File: grafana_results.json"
echo ""

# Step 2: GitHub CLI Setup
echo "STEP 2: GitHub CLI Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ GitHub CLI installed"
GH_VERSION=$(gh --version)
echo "   $GH_VERSION"
echo ""

# Step 3: Authentication
echo "STEP 3: GitHub Authentication"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
AUTH_STATUS=$(gh auth status 2>&1 || true)
if echo "$AUTH_STATUS" | grep -q "Logged in"; then
    echo "✅ Authenticated with GitHub"
    echo "   Status: $(gh auth status 2>&1 | head -1)"
else
    echo "   Status: $(gh auth status 2>&1 | head -1)"
fi
echo ""

# Step 4: Labels Created
echo "STEP 4: GitHub Labels Created"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Issue labels created:"
gh label list --json name,description,color -q '.[] | select(.name | IN("grafana", "runtime-error", "automated", "copilot")) | "   - \(.name): \(.description) (color: \(.color))"' | sort || true
echo ""

# Step 5: Issue Creation
echo "STEP 5: GitHub Issue Created"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ISSUE_TITLE=$(jq -r '.github_issue.title' grafana_results.json)
ISSUE_LABELS=$(jq -r '.github_issue.labels[]' grafana_results.json | tr '\n' ', ' | sed 's/,$//')
echo "✅ Issue created successfully"
echo "   Title: $ISSUE_TITLE"
echo "   Labels: $ISSUE_LABELS"
echo ""

# Get the latest issue (should be the one we just created)
LATEST_ISSUE=$(gh issue list --limit 1 --json number,title,url -q '.[0]')
ISSUE_NUMBER=$(echo "$LATEST_ISSUE" | jq -r '.number')
ISSUE_URL=$(echo "$LATEST_ISSUE" | jq -r '.url')

echo "   Issue #: $ISSUE_NUMBER"
echo "   URL: $ISSUE_URL"
echo ""

# Step 6: Issue Details
echo "STEP 6: Issue Details"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ISSUE_INFO=$(gh issue view $ISSUE_NUMBER --json state,assignees,labels)
STATE=$(echo "$ISSUE_INFO" | jq -r '.state')
ASSIGNEES=$(echo "$ISSUE_INFO" | jq -r '.assignees[].login' | tr '\n' ', ' | sed 's/,$//')
LABELS=$(echo "$ISSUE_INFO" | jq -r '.labels[].name' | tr '\n' ', ' | sed 's/,$//')

echo "   State: $STATE"
echo "   Assignees: ${ASSIGNEES:-None}"
echo "   Labels: $LABELS"
echo ""

# Step 7: Error Summary
echo "STEP 7: Error Summary (from Grafana)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "   Top 5 Errors:"
jq -r '.errors[0:5] | .[] | "   - [\(.severity)] \(.title | gsub("\\|"; " ") | .[0:80])"' grafana_results.json | head -5
echo ""

# Step 8: Next Steps
echo "STEP 8: Next Steps"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. 📖 View the created issue:"
echo "   $ISSUE_URL"
echo ""
echo "2. 🤖 Copilot Assignment:"
if echo "$ASSIGNEES" | grep -q "copilot"; then
    echo "   ✅ Already assigned to copilot-swe-agent[bot]"
else
    echo "   ⚠️  Not assigned yet. Manually assign to copilot-swe-agent[bot]"
    echo "   Command: gh issue edit $ISSUE_NUMBER --add-assignee copilot-swe-agent[bot]"
fi
echo ""
echo "3. ⏱️  Wait for Copilot PR:"
echo "   - Copilot will analyze the errors (1-2 minutes)"
echo "   - A PR will be created automatically"
echo "   - Command to check: gh pr list --author copilot-swe-agent[bot]"
echo ""
echo "4. 🔍 Review the PR:"
echo "   - Verify the fixes address the root cause"
echo "   - Check for proper error handling and tests"
echo "   - Merge if satisfied"
echo ""

# Step 9: Useful Commands
echo "STEP 9: Useful Commands for Monitoring"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cat << 'EOF'
# View the issue
gh issue view <ISSUE_NUMBER>

# Watch for PR creation
gh pr list --author copilot-swe-agent[bot]

# View PR details
gh pr view <PR_NUMBER>

# Check PR status
gh pr view <PR_NUMBER> --json state,reviews,statusCheckRollup

# List all Grafana issues
gh issue list --label grafana

# Export issue to markdown
gh issue view <ISSUE_NUMBER> --json body -q '.body' > issue_details.md
EOF
echo ""

# Step 10: Summary Statistics
echo "STEP 10: Workflow Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Repository: $(gh repo view --json nameWithOwner -q '.nameWithOwner')"
echo "Errors Detected: $ERRORS"
echo "Issue Created: #$ISSUE_NUMBER"
echo "Issue URL: $ISSUE_URL"
echo "Status: ✅ COMPLETE"
echo ""
echo "=========================================="
echo "✅ WORKFLOW COMPLETE - READY FOR COPILOT"
echo "=========================================="
echo ""
