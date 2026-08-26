#!/usr/bin/env bash
# Acceptance test for "does elfmem actually work when installed as a real
# dependency" — the check `uv build` alone (CI's existing "Build check" job)
# doesn't answer. That job only confirms the wheel/sdist build without
# errors; it never installs the artifact and exercises it. The `test` job
# doesn't either — `uv sync --extra dev` is an editable dev install, which
# can silently pass even if a packaged data file (a prompt template, the
# guide registry) is missing from the wheel's MANIFEST.
#
# This builds the wheel, installs it into a throwaway venv in a directory
# with no access to this repo's src/, and drives it through the full stack
# a real consumer would use: init → doctor → remember → dream → recall →
# agent-docs install → MCP server startup. All from the installed package
# only — never `uv run`, never PYTHONPATH into this repo.
#
# Usage: scripts/smoke_install_test.sh
#   Requires LM Studio (or another OpenAI-compatible local server) running
#   at the URL/model this script configures below — see README's "Local
#   models" section. Point SMOKE_LLM_BASE_URL/SMOKE_LLM_MODEL at a
#   different provider to test against a different backend.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

SMOKE_LLM_BASE_URL="${SMOKE_LLM_BASE_URL:-http://localhost:1234/v1}"
SMOKE_LLM_MODEL="${SMOKE_LLM_MODEL:-google/gemma-4-26b-a4b}"
SMOKE_EMBED_MODEL="${SMOKE_EMBED_MODEL:-text-embedding-nomic-embed-text-v1.5}"

echo "== Building the real distributable artifact =="
cd "$ROOT_DIR"
rm -rf dist/
uv build --quiet
WHEEL="$(cd dist && pwd)/$(ls dist/*.whl | xargs -n1 basename)"
echo "Built: $WHEEL"

echo "== Installing into an isolated venv, no dev extras, no src/ access =="
PROJECT_DIR="$WORK_DIR/other_project"
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"
git init --quiet   # a real project marker -- elfmem's project detection needs one
uv venv --quiet
uv pip install "${WHEEL}[tools]" --quiet

echo "== elfmem --version =="
.venv/bin/elfmem --version

echo "== elfmem init (fresh project, no dev config carried over) =="
# --db pinned inside WORK_DIR, not left to project-name inference: the DB
# itself is namespaced globally by project name (~/.elfmem/databases/<name>),
# not sandboxed inside the project directory the way config.yaml is. Without
# this, a fixed directory basename (PROJECT_DIR is always "other_project")
# means every run of this script shares the same DB and accumulates state
# across runs -- caught live: a second run's differently-timestamped test
# fact got flagged as a near-duplicate of the first run's and silently
# deduped instead of promoted, exactly the class of bug an isolated test is
# supposed to catch, this time in the test itself.
.venv/bin/elfmem init --db "$WORK_DIR/isolated.db" >/dev/null
[[ -f .elfmem/config.yaml ]] || { echo "FAIL: no project-local config.yaml written"; exit 1; }

# Point at the configured local backend -- appended, not templated in, so
# this stays close to what a real consumer's config edit looks like.
cat >> .elfmem/config.yaml <<EOF

llm:
  model: "${SMOKE_LLM_MODEL}"
  base_url: "${SMOKE_LLM_BASE_URL}"

embeddings:
  model: "${SMOKE_EMBED_MODEL}"
  base_url: "${SMOKE_LLM_BASE_URL}"
  dimensions: 768
EOF

echo "== elfmem doctor =="
# `doctor` legitimately exits non-zero at this point -- setup is genuinely
# incomplete by its own definition (no DB write yet, no MCP entry, this
# being a headless smoke test rather than a real Claude Code session).
# `command | grep` under pipefail would fail the whole script on that exit
# code alone regardless of what grep finds, so the check is on captured
# *content*, not on doctor's own exit status.
doctor_output="$(.venv/bin/elfmem doctor || true)"
echo "$doctor_output" | grep -q "Config.*project-local" \
  || { echo "FAIL: doctor doesn't see the project-local config"; echo "$doctor_output"; exit 1; }

echo "== Full round trip: remember -> dream -> recall =="
export OPENAI_API_KEY="${OPENAI_API_KEY:-not-needed}"
.venv/bin/elfmem remember "Smoke test marker: $(date +%s)." \
  --cue "verifying the smoke_install_test round trip" >/dev/null
dream_output="$(.venv/bin/elfmem dream --no-llm || true)"
echo "$dream_output" | grep -q "1 promoted" \
  || { echo "FAIL: consolidation didn't promote the test block"; echo "$dream_output"; exit 1; }
recall_output="$(.venv/bin/elfmem recall "smoke test marker" || true)"
echo "$recall_output" | grep -q "Smoke test marker" \
  || { echo "FAIL: recall didn't return the block just promoted"; echo "$recall_output"; exit 1; }

echo "== agent-docs install (packaged guide/prompt data must be present) =="
docs_output="$(.venv/bin/elfmem agent-docs install || true)"
echo "$docs_output" | grep -q "AGENT.md" \
  || { echo "FAIL: agent-docs install didn't render AGENT.md"; echo "$docs_output"; exit 1; }

echo "== MCP server starts and stays up =="
.venv/bin/elfmem serve > /tmp/smoke_serve.log 2>&1 &
SERVE_PID=$!
sleep 2
if ! kill -0 "$SERVE_PID" 2>/dev/null; then
  echo "FAIL: MCP server exited early -- log:"
  cat /tmp/smoke_serve.log
  exit 1
fi
kill "$SERVE_PID"

echo
echo "PASS: elfmem installs and works end-to-end as a real dependency."
