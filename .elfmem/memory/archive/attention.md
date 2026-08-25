## Squash-merge trap: after GitHub squash-merges a PR, the sour
<!-- id: b2187defde97dee8  cls: status  pinned: false  created: 2026-04-28T21:58:15.915175+00:00 -->

Squash-merge trap: after GitHub squash-merges a PR, the source branch's original commits have different SHAs than the single squash commit on main. Rebasing or pulling the source branch onto updated main causes conflicts — git sees the original commits as 'new' work conflicting with their own squashed content. Fix: never rebase a squash-merged branch onto main. After merge, the branch is dead — delete it immediately (git branch -d <branch> && git push origin --delete <branch>). If you accidentally start a rebase, abort with git rebase --abort. The only safe operation is git checkout main && git reset --hard origin/main to sync local main.

## elfmem release checklist (tested v0.7.0, 2026-04-28):
<!-- id: 6aa414d49a4e39d8  cls: status  pinned: false  created: 2026-04-28T21:58:37.412994+00:00 -->

elfmem release checklist (tested v0.7.0, 2026-04-28):

1. PREP: Ensure all feature PRs are merged to main. Sync local main: git fetch origin && git checkout main && git reset --hard origin/main
2. BRANCH: git checkout -b release/vX.Y.Z — never commit directly to main
3. VERSION BUMP: Update pyproject.toml line 7 (version = 'X.Y.Z'). Update CHANGELOG.md: rename [Unreleased] to [X.Y.Z] — YYYY-MM-DD
4. VERIFY: uv run ruff check src/ tests/ && uv run mypy src/elfmem/ && uv run pytest -q
5. COMMIT & PUSH: git add pyproject.toml CHANGELOG.md && git commit -m 'Release vX.Y.Z' && git push origin release/vX.Y.Z
6. PR: gh pr create --title 'Release vX.Y.Z' targeting main. Wait for CI green.
7. MERGE: Merge PR on GitHub (squash or merge — either works for a single commit)
8. TAG: git fetch origin && git checkout main && git reset --hard origin/main && git tag -a vX.Y.Z -m 'Release X.Y.Z' && git push origin vX.Y.Z
9. CLEANUP: git branch -d release/vX.Y.Z (local). GitHub auto-deletes remote on merge.
10. VERIFY: gh run list --limit 3 — confirm Publish to PyPI workflow triggered and succeeds.

Critical: step 8 MUST happen after merge, on main, at the merge commit. The v*.*.* tag triggers publish.yml (PyPI via OIDC trusted publishing). Version in pyproject.toml must match the tag.
