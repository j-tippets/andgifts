#!/usr/bin/env bash
# Applies a patch file on a fresh branch off main and pushes it.
#
# Usage:
#   ./apply-patch.sh path/to/0019.patch
#   ./apply-patch.sh path/to/0019.patch my-branch-name
#
# If you don't pass a branch name, it's derived from the patch filename
# (0019.patch -> 0019).

set -euo pipefail

PATCH_FILE="${1:?Usage: $0 <patch-file> [branch-name]}"
BRANCH_NAME="${2:-$(basename "$PATCH_FILE" .patch)}"

if [ ! -f "$PATCH_FILE" ]; then
  echo "Patch file not found: $PATCH_FILE" >&2
  exit 1
fi

# Resolve to an absolute path *before* switching branches/directories,
# since a relative path could stop resolving correctly once we've
# checked out a different branch.
PATCH_FILE="$(cd "$(dirname "$PATCH_FILE")" && pwd)/$(basename "$PATCH_FILE")"

echo "== Updating main =="
git checkout main
git pull origin main

echo "== Creating branch: $BRANCH_NAME =="
git checkout -b "$BRANCH_NAME"

echo "== Applying $PATCH_FILE =="
git am -3 "$PATCH_FILE"

echo "== Pushing $BRANCH_NAME =="
git push -u origin "$BRANCH_NAME"

echo "Done. Branch '$BRANCH_NAME' is pushed and up to date."
