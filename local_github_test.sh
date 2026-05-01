#!/bin/bash
# Local GitHub Issue Creation Test Script
# Tests the PR creation flow without running the full agent

set -e

echo "🧪 Testing GitHub Issue Creation Flow..."
echo ""

# Check if gh CLI is installed
if ! command -v gh &> /dev/null; then
    echo "❌ GitHub CLI (gh) is not installed"
    echo "   Install it: https://cli.github.com/"
    exit 1
fi

# Check GitHub authentication
if ! gh auth status &> /dev/null; then
    echo "❌ Not authenticated with GitHub"
    echo "   Run: gh auth login"
    exit 1
fi

echo "✅ GitHub CLI authenticated"
echo ""

# Check if grafana_results.json exists
if [ ! -f "grafana_results.json" ]; then
    echo "❌ grafana_results.json not found"
    echo "   Run agent first: python -m agent.grafana_error_agent --output grafana_results.json"
    exit 1
fi

echo "✅ Found grafana_results.json"
echo ""

# Extract results
ERRORS_FOUND=$(jq -r '.errors_found // 0' grafana_results.json)
TITLE=$(jq -r '.github_issue.title // "No title"' grafana_results.json)
LABELS=$(jq -r '.github_issue.labels[]' grafana_results.json 2>/dev/null | tr '\n' ',' | sed 's/,$//')

echo "📊 Results Summary:"
echo "   Errors Found: $ERRORS_FOUND"
echo "   Issue Title: $TITLE"
echo "   Labels: $LABELS"
echo ""

if [ "$ERRORS_FOUND" == "0" ]; then
    echo "⚠️  No errors found. Skipping issue creation."
    exit 0
fi

# Ask user for confirmation
echo "⚠️  This will CREATE a REAL GitHub issue in your repository!"
read -p "Continue? (yes/no): " -n 3 -r REPLY
echo
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    echo "❌ Cancelled"
    exit 1
fi

# Extract issue body
jq -r '.github_issue.body' grafana_results.json > /tmp/grafana_issue_body.md

echo ""
echo "📝 Creating GitHub issue..."
echo "   Title: $TITLE"
echo "   Labels: $LABELS"
echo ""

# Create the issue using gh CLI
ISSUE_URL=$(gh issue create \
    --title "$TITLE" \
    --body-file /tmp/grafana_issue_body.md \
    --label "grafana" \
    --label "runtime-error" \
    --label "automated" \
    --label "copilot" \
    2>&1) || true

if [[ "$ISSUE_URL" != http* ]]; then
    echo "❌ Failed to create issue"
    echo "   Response: $ISSUE_URL"
    exit 1
fi

echo "✅ Issue created successfully!"
echo "   URL: $ISSUE_URL"
echo ""

# Extract issue number
ISSUE_NUMBER=$(echo "$ISSUE_URL" | grep -oE '[0-9]+$')
REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner')

echo "🤖 Attempting to assign to Copilot..."
echo ""

# Try to assign to copilot-swe-agent[bot]
ASSIGN_RESULT=$(gh api \
    --method PATCH \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "/repos/$REPO/issues/$ISSUE_NUMBER" \
    -f "assignees[]=copilot-swe-agent[bot]" \
    2>&1) || true

# Check if assignment succeeded
if echo "$ASSIGN_RESULT" | jq -e '.assignees[]? | select(.login == "copilot-swe-agent[bot]")' > /dev/null 2>&1; then
    echo "✅ Assigned to copilot-swe-agent[bot]"
elif echo "$ASSIGN_RESULT" | jq -e '.number' > /dev/null 2>&1; then
    echo "⚠️  Issue updated, but Copilot assignment status unclear"
    echo "   Check the issue page for assignment status"
else
    echo "⚠️  Copilot assignment failed"
    echo "   You may need to manually assign the issue"
    echo "   Response: $(echo "$ASSIGN_RESULT" | head -c 200)"
fi

echo ""
echo "✅ Test Complete!"
echo ""
echo "📌 Next Steps:"
echo "   1. View the created issue: $ISSUE_URL"
echo "   2. If Copilot is assigned, it will open a PR with fixes"
echo "   3. Review the PR for accuracy before merging"
echo ""
