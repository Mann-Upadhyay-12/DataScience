#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

REMOTE_URL="git@github.com:Mann-Upadhyay-12/DataScience.git"
BRANCH="main"

echo "=== Preparing DataScience Git Repository ==="

# Ensure git remote is configured
if ! git remote get-url origin >/dev/null 2>&1; then
    echo "Adding remote origin..."
    git remote add origin "$REMOTE_URL"
else
    git remote set-url origin "$REMOTE_URL"
fi

# Show status
git status -s

echo "=== Ready to Push ==="
echo "Target remote: $REMOTE_URL"
echo "Target branch: $BRANCH"

read -p "Do you want to run 'git push -u origin $BRANCH' now? (y/N) " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    git push -u origin "$BRANCH"
else
    echo "Push aborted. Whenever you're ready, execute:"
    echo "  git push -u origin $BRANCH"
fi
