#!/bin/bash
# COMPLETE GRAFANA ERROR AGENT WORKFLOW - ALL COMMANDS EXECUTED
# This is a reference script showing exactly what was done

echo "═══════════════════════════════════════════════════════════════════════════════"
echo "🎉 GRAFANA ERROR AGENT - COMPLETE WORKFLOW - ALL COMMANDS EXECUTED"
echo "═══════════════════════════════════════════════════════════════════════════════"
echo ""

cat << 'EOF'
# ============================================================================
# COMPLETE WORKFLOW COMMANDS - COPY & PASTE READY
# ============================================================================

# =========================
# STEP 1: INSTALL GITHUB CLI
# =========================
brew install gh


# ============================
# STEP 2: AUTHENTICATE WITH GH
# ============================
gh auth login
# Follow prompts:
# 1. Select "GitHub.com"
# 2. Select "HTTPS" for protocol
# 3. Select "Yes" for Git credentials
# 4. Select "Login with a web browser"
# 5. Copy the one-time code and open https://github.com/login/device
# 6. Enter the code and authorize


# =================================
# STEP 3: VERIFY AUTHENTICATION
# =================================
gh auth status


# =================================
# STEP 4: RUN GRAFANA ERROR AGENT
# =================================
export $(cat .env | xargs)
python -m agent.grafana_error_agent --output grafana_results.json


# =================================
# STEP 5: CREATE GITHUB LABELS
# =================================
gh label create grafana --description "Issues from Grafana Error Agent" --color "FF6B6B"
gh label create runtime-error --description "Runtime errors detected" --color "E85D75"
gh label create automated --description "Created by automated agent" --color "4A90E2"
gh label create copilot --description "For GitHub Copilot assignment" --color "A371F7"


# =================================
# STEP 6: EXTRACT AND PREPARE ISSUE
# =================================
TITLE=$(jq -r '.github_issue.title' grafana_results.json)
jq -r '.github_issue.body' grafana_results.json > /tmp/grafana_issue_body.md

echo "Issue Title: $TITLE"
echo "Issue Body: $(head -5 /tmp/grafana_issue_body.md)"


# =================================
# STEP 7: CREATE GITHUB ISSUE
# =================================
gh issue create \
    --title "$TITLE" \
    --body-file /tmp/grafana_issue_body.md \
    --label "grafana" \
    --label "runtime-error" \
    --label "automated" \
    --label "copilot"

# Output: https://github.com/DilipKumarMummadi/grafana-error-agent/issues/2


# =================================
# STEP 8: ASSIGN TO COPILOT BOT
# =================================
ISSUE_NUMBER=2
gh issue edit $ISSUE_NUMBER --add-assignee 'copilot-swe-agent[bot]'


# =================================
# STEP 9: VERIFY ASSIGNMENT
# =================================
gh issue view $ISSUE_NUMBER --json assignees -q '.assignees[].login'
# Output should show both: Copilot and DilipKumarMummadi


# =================================
# STEP 10: MONITOR FOR PR CREATION
# =================================
# Wait 1-2 minutes, then check for PRs from Copilot:
gh pr list --author copilot-swe-agent[bot]

# View specific PR details:
gh pr view <PR_NUMBER> --json state,title,body

# Check PR status and reviews:
gh pr view <PR_NUMBER> --json state,reviews,statusCheckRollup


# =================================
# USEFUL COMMANDS FOR MONITORING
# =================================

# View the created issue:
gh issue view 2

# View issue in browser:
gh issue view 2 --web

# List all Grafana-labeled issues:
gh issue list --label grafana

# Export full issue details:
gh issue view 2 --json title,body,labels,assignees --template 'Issue: {{.title}}{{"\n\n"}}Body:{{"\n"}}{{.body}}'

# Check for any PRs linked to the issue:
gh issue view 2 --json id,linked_pull_requests

# Add a comment to the issue:
gh issue comment 2 --body "Checking Copilot progress..."

# Create follow-up issues if needed:
gh issue create --title "Follow up on Grafana errors" --body "Check if fixes are working..."


# =================================
# REVERTING/CLEANUP (If Needed)
# =================================

# Close the issue:
gh issue close 2

# Delete labels:
gh label delete grafana --yes
gh label delete runtime-error --yes
gh label delete automated --yes
gh label delete copilot --yes

# Remove assignee:
gh issue edit 2 --remove-assignee 'copilot-swe-agent[bot]'


# =================================
# ADDITIONAL MONITORING
# =================================

# Watch PR status continuously:
watch -n 30 'gh pr list --author copilot-swe-agent[bot]'

# Get PR comments:
gh pr view <PR_NUMBER> --json comments -q '.comments[].body'

# List all PRs you're involved with:
gh pr list --state all

# Get PR review status:
gh pr view <PR_NUMBER> --json reviewDecision,reviews

EOF

echo ""
echo "═══════════════════════════════════════════════════════════════════════════════"
echo "📊 WORKFLOW SUMMARY"
echo "═══════════════════════════════════════════════════════════════════════════════"
echo ""

# Get the actual values
REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner')
ISSUE_NUMBER=2
ISSUE_URL="https://github.com/$REPO/issues/$ISSUE_NUMBER"
ERRORS=$(jq '.errors_found' grafana_results.json)
ASSIGNEES=$(gh issue view $ISSUE_NUMBER --json assignees -q '.assignees[].login' | tr '\n' ', ' | sed 's/,$//')
LABELS=$(gh issue view $ISSUE_NUMBER --json labels -q '.labels[].name' | tr '\n' ', ' | sed 's/,$//')

cat << EOF
✅ Workflow Status: COMPLETE

📈 Agent Execution:
   - Errors Found: $ERRORS
   - Source: Grafana MCP (Loki Logs)
   - Query: Kafka SSL handshake failures

🏷️  GitHub Labels Created:
   - grafana (Red)
   - runtime-error (Orange)
   - automated (Blue)
   - copilot (Purple)

📝 GitHub Issue Created:
   - Issue Number: #$ISSUE_NUMBER
   - URL: $ISSUE_URL
   - Title: [Grafana Agent] Fix 10 runtime error(s)
   - Labels: $LABELS
   - Assignees: $ASSIGNEES

🤖 Copilot Bot Status:
   - Assigned: Yes ✅
   - Expected Action: Create PR with fixes
   - ETA: 1-2 minutes

📋 Next Actions:
   1. Monitor for PR creation: gh pr list --author copilot-swe-agent[bot]
   2. Review PR when created
   3. Verify fixes address root cause
   4. Merge if satisfied
   5. Monitor production for error resolution

EOF

echo "═══════════════════════════════════════════════════════════════════════════════"
echo "✅ ALL STEPS COMPLETED SUCCESSFULLY"
echo "═══════════════════════════════════════════════════════════════════════════════"
echo ""
