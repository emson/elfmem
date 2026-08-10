#!/usr/bin/env python3
"""Validate a build-plan.md and emit plan.lock.yaml.

The model proposes; this disposes. Everything checked here is a set operation, a
graph algorithm, or a regex -- no judgement. Judgement stays with the human.

Usage:
    python3 validate_plan.py <plan-dir> [--spec <spec.md>] [--model <model.md>]
                                        [--map <map.md>] [--write-lock] [--strict]

    <plan-dir>   directory holding build-plan.md (e.g. spec/plan)
    --spec       spec file, for coverage checking and content hashing
    --model      concept model (default: <plan-dir>/model.md if it exists)
    --map        environment map (default: <plan-dir>/map.md if it exists)
    --write-lock emit plan.lock.yaml (default: report only)
    --strict     promote concept-coverage warnings to errors

Exit 0 = clean (warnings allowed), 1 = errors.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

VERSION = "1.2.0"

UNIT_RE = re.compile(r"^##\s+(U-\d+)\s*:\s*(.+?)\s*$", re.M)
FIELD_RE = re.compile(r"^\*\*([A-Za-z][A-Za-z ]*?):\*\*\s*(.*?)\s*$", re.M)
ANCHOR_RE = re.compile(r"(?:TEST-[A-Z0-9]+-\d+|D-\d+|§[^;,]+)")
NONE_RE = re.compile(r"^\(?\s*(none|n/?a|-)\s*\)?$", re.I)

# Touches is required even when empty: omitting it is the easy way to look
# parallel-safe without having thought about it. Authors must write "(none)".
REQUIRED = ["Wave", "Execution", "Owns", "Touches", "Needs", "Implements",
            "Done when", "Verified by"]

# Contract depth is proportional to risk and inverse to distance -- but until this
# existed, every parsed unit needed all eight fields regardless of distance, so a
# "sketched" distant unit either failed validation or got written out in full. Two
# recorded runs resolved it the same way, by writing every unit in full, which is
# the false precision the horizon rule exists to prevent.
#
# Coverage is unaffected: `Implements` is required at every depth, so every spec
# claim still has a home from day one. Only the design detail is lazy.
DEPTHS = {
    "full": REQUIRED,
    "sketched": ["Wave", "Owns", "Needs", "Implements"],
    "named": ["Wave", "Implements"],
}
EXECUTIONS = {"parallel-safe", "sequential", "integration"}

# --- Gate-command anatomy -------------------------------------------------
# A `Verified by` string is a shell pipeline of sub-commands. Only the test
# sub-commands can carry a path filter; lint/typecheck/build gates are
# repo-wide by nature and are skipped rather than guessed at.
TEST_CMD_RE = re.compile(r"\b(test|vitest|jest|pytest|mocha|rspec|phpunit|ava)\b", re.I)

# Tokens that introduce the runner rather than a filter.
RUNNER_TOKENS = {"npm", "pnpm", "yarn", "npx", "bun", "deno", "go", "cargo",
                 "poetry", "uv", "make", "just", "run", "exec", "--"}

# Flags whose *value* is a name/marker expression, not a path. Skipping both
# the flag and its value is what keeps `cargo test -p solver` and
# `pytest -k slow` silent instead of falsely reporting a gate that filters
# nothing. Silence when unsure: a false positive here teaches people to
# ignore every other check in this file.
NAME_FLAGS = {"-k", "-m", "-p", "--package", "-run", "--run-name", "-t",
              "--grep", "--testNamePattern", "--name", "--filter"}

SEAM_PROTOCOLS = ("sole owner", "deferred wiring", "append-only",
                  "growable by injection")

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$", re.M)

# A collective heading ("## Modules") owns its immediate children as concepts.
# "## Module: Foo" is the other common shape and yields one concept directly.
COLLECTIVE_RE = re.compile(
    r"\b(modules|components|subsystems|capabilities|concepts|building blocks)\b", re.I)
PREFIXED_RE = re.compile(
    r"^(?:module|component|subsystem)\s*[:–—-]\s*(.+)$", re.I)

# Headings that are structure, not concepts. Filtered even inside a module section.
BOILERPLATE_RE = re.compile(
    r"^(overview|introduction|summary|goals?|non.?goals?|scope|out of scope|"
    r"background|motivation|glossary|terminology|appendix|references?|"
    r"changelog|revision history|table of contents|contents|open questions?|"
    r"risks?|assumptions?|decisions?|decision log|testing|test specs?|test plan|"
    r"acceptance criteria|notes?|rationale|future work|examples?|template|"
    r"module template|build sequence|build order|status)\b", re.I)

# The spec's own build ordering: validate it, never trust it (Stage 0).
SPEC_ORDER_RE = re.compile(
    r"^#{1,6}\s*[\d.\s]*((?:build|implementation|delivery)\s+"
    r"(?:sequence|order|plan|phases?)|phasing|increment ladder|waves?)\b.*$",
    re.M | re.I)

TRACER_SECTION_RE = re.compile(r"^#{2,4}\s*mechanisms?\s+in\s+the\s+tracer\s*$",
                               re.M | re.I)

# Dropped before matching: they carry no discriminating signal in a concept name.
NOISE_TOKENS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with",
    "module", "modules", "manager", "engine", "service", "system", "layer",
    "component", "subsystem", "handler", "controller", "provider", "helper",
}

errors: list[str] = []
warnings: list[str] = []
STRICT = False


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def coverage_finding(msg: str) -> None:
    """Concept coverage is fuzzy name matching, so it warns by default. --strict
    promotes it, for the run that gates a version of the plan."""
    (err if STRICT else warn)(msg)


def is_none(value: str) -> bool:
    return not value or bool(NONE_RE.match(value))


def split_list(value: str) -> list[str]:
    if is_none(value):
        return []
    return [p.strip().strip("`") for p in re.split(r"[,;]", value) if p.strip()]


def parse_units(text: str) -> dict[str, dict]:
    """Each `## U-NNN: name` heading owns the text up to the next heading."""
    units: dict[str, dict] = {}
    matches = list(UNIT_RE.finditer(text))
    for i, m in enumerate(matches):
        uid, name = m.group(1), m.group(2)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        if uid in units:
            err(f"duplicate unit id {uid}")
            continue
        fields = {k.strip(): v.strip() for k, v in FIELD_RE.findall(body)}
        units[uid] = {"id": uid, "name": name, "fields": fields}
    return units


def glob_conflict(a: str, b: str) -> bool:
    """Conservative overlap test on path patterns: prefix containment before any wildcard."""
    pa, pb = a.split("*")[0].rstrip("/"), b.split("*")[0].rstrip("/")
    if not pa or not pb:
        return True
    return pa == pb or pa.startswith(pb + "/") or pb.startswith(pa + "/")


def norm_path(p: str) -> str:
    """Compare paths as their fixed prefix: everything before the first wildcard."""
    p = p.strip().strip("`\"'").lstrip("./")
    return p.split("*")[0].rstrip("/")


def path_covered_by(path: str, pattern: str) -> bool:
    """Does an `Owns` pattern cover a concrete path? Prefix containment, since a
    pattern's fixed part is the directory it claims."""
    a, b = norm_path(path), norm_path(pattern)
    if not a or not b:
        return False
    return a == b or a.startswith(b + "/")


def owners_of(path: str, units: dict[str, dict]) -> list[str]:
    return sorted(uid for uid, u in units.items()
                  if any(path_covered_by(path, p)
                         for p in split_list(u["fields"].get("Owns", ""))))


def gate_filters(verified: str) -> list[str] | None:
    """Path filters a gate command applies, or None when it applies none.

    None means 'this gate is repo-wide, so it covers everything' -- which is
    also what a command this parser cannot confidently read returns, on
    purpose. An empty list means 'a test sub-command ran, filtered by nothing'.
    """
    text = verified.strip().strip("`")
    if not text:
        return None
    saw_test_cmd = False
    filters: list[str] = []
    for sub in re.split(r"&&|\|\||;|\|", text):
        tokens = sub.split()
        if not tokens or not TEST_CMD_RE.search(sub):
            continue
        saw_test_cmd = True
        skip_next = False
        for tok in tokens[1:]:
            if skip_next:
                skip_next = False
                continue
            if tok in NAME_FLAGS:
                skip_next = True
                continue
            if tok.startswith("-"):
                continue
            if tok.lower() in RUNNER_TOKENS or TEST_CMD_RE.fullmatch(tok):
                continue
            # npm-style script names ("test:unit", "test-watch") name the
            # runner, not a filter.
            if re.match(r"^test[:_-]", tok, re.I):
                continue
            filters.append(tok.strip("`\"'"))
    if not saw_test_cmd:
        return None
    return filters


def filter_selects(filt: str, owned: str) -> bool:
    """A filter selects an owned path when either contains the other. Deliberately
    loose: `kernel/solver` selects `src/kernel/solver/**`, and `skin` selects
    `client/src/skin/**`."""
    f, o = norm_path(filt), norm_path(owned)
    if not f or not o:
        return False
    return f in o or o in f


def check_verified_by_coverage(units: dict[str, dict]) -> None:
    """A unit's gate must exercise the unit's own files.

    Closure, checked: `Owns`, `Done when` and `Verified by` are authored as three
    independent fields and nothing else forces them to describe the same unit. In
    the reference build 5 of 33 units carried a filter that silently skipped some
    of their own tests.
    """
    for uid, u in sorted(units.items()):
        owned = split_list(u["fields"].get("Owns", ""))
        filters = gate_filters(u["fields"].get("Verified by", ""))
        if not owned or filters is None or not filters:
            continue
        missed = [p for p in owned
                  if not any(filter_selects(f, p) for f in filters)]
        if len(missed) == len(owned):
            err(f"{uid}: 'Verified by' filters {filters} select none of its own "
                f"owned paths {owned} -- this gate passes without running any of "
                f"the work the unit is contracted to do")
        elif missed:
            warn(f"{uid}: 'Verified by' filters {filters} do not select {missed} "
                 f"-- those owned paths are never exercised by the unit's own gate")


def check_global_ownership(units: dict[str, dict]) -> None:
    """Across the whole plan a path should resolve to exactly one owner.

    check_parallel_safety errors on same-wave overlap because it destroys work.
    Cross-wave overlap merely makes 'who owns this file' unanswerable, which is
    what makes the cross-document check below ambiguous -- so it warns.
    """
    items = sorted(units.items())
    for i, (uid_a, a) in enumerate(items):
        for uid_b, b in items[i + 1:]:
            if a["fields"].get("Wave") == b["fields"].get("Wave"):
                continue  # already an error in check_parallel_safety
            for pa in split_list(a["fields"].get("Owns", "")):
                for pb in split_list(b["fields"].get("Owns", "")):
                    if glob_conflict(pa, pb):
                        warn(f"{uid_a} (wave {a['fields'].get('Wave')}) and {uid_b} "
                             f"(wave {b['fields'].get('Wave')}) both own overlapping "
                             f"paths ({pa} / {pb}) -- ownership is global, so one of "
                             f"them should narrow its claim")


def parse_seams(model_text: str) -> list[tuple[str, str, str]]:
    """Rows of model.md's Seams table as (path, owner unit id, protocol)."""
    body = section_text(model_text, r"^seams\b")
    rows: list[tuple[str, str, str]] = []
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or set("".join(cells)) <= set("-: "):
            continue
        if cells[0].lower() in {"seam", "name"}:
            continue
        path = cells[1].strip("`*_ ")
        owner = re.search(r"U-\d+", cells[2])
        if not path or "<" in path:
            continue
        rows.append((path, owner.group(0) if owner else "", cells[3]))
    return rows


def check_cross_document_ownership(units: dict[str, dict],
                                   model: Path | None) -> None:
    """model.md's Seams table and the units' `Owns` must agree on who owns a path.

    Two documents asserting one fact about one path is a contradiction, not a
    matter of interpretation, so disagreement is an error. Found one real case in
    the reference build, and only because somebody happened to read both files.
    """
    if not model or not model.exists():
        return
    rows = parse_seams(model.read_text(encoding="utf-8", errors="replace"))
    if not rows:
        return
    for path, declared, _protocol in rows:
        actual = owners_of(path, units)
        if not declared:
            err(f"seam {path!r} in model.md names no owning unit -- seams are "
                f"exactly where work goes unowned, so each gets one owner")
            continue
        if declared not in units:
            err(f"seam {path!r} in model.md is owned by {declared}, which is not a "
                f"unit in build-plan.md")
            continue
        if not actual:
            err(f"seam {path!r} is owned by {declared} in model.md, but no unit's "
                f"'Owns' covers that path in build-plan.md")
        elif declared not in actual:
            err(f"ownership contradiction for {path!r}: model.md says {declared}, "
                f"build-plan.md says {' and '.join(actual)}")


def check_cross_wave_touches(units: dict[str, dict], model: Path | None) -> None:
    """A `Touches` path owned by an *earlier* wave is a permanent cross-owner edit.

    `Touches` is scoped to same-wave wiring, which an integration unit resolves
    once. Reaching back into a wave that has already shipped is a different thing:
    the edit recurs for every future unit that needs it. The fix is an extension
    point in the owning unit, declared as the seam's protocol.
    """
    seam_protocols: dict[str, str] = {}
    if model and model.exists():
        for path, _owner, protocol in parse_seams(
                model.read_text(encoding="utf-8", errors="replace")):
            seam_protocols[norm_path(path)] = protocol.lower()

    for uid, u in sorted(units.items()):
        try:
            mine = float(u["fields"].get("Wave", "0"))
        except ValueError:
            continue
        for touched in split_list(u["fields"].get("Touches", "")):
            for owner in owners_of(touched, units):
                if owner == uid:
                    continue
                try:
                    theirs = float(units[owner]["fields"].get("Wave", "0"))
                except ValueError:
                    continue
                if theirs >= mine:
                    continue
                protocol = ""
                for seam_path, proto in seam_protocols.items():
                    if seam_path and (seam_path in norm_path(touched)
                                      or norm_path(touched) in seam_path):
                        protocol = proto
                        break
                if any(p in protocol for p in SEAM_PROTOCOLS[1:]):
                    continue
                warn(f"{uid} (wave {u['fields'].get('Wave')}) touches {touched}, "
                     f"owned by {owner} in the earlier wave "
                     f"{units[owner]['fields'].get('Wave')} -- every later unit needing this "
                     f"will edit it again. Have {owner} ship an extension point and "
                     f"record the seam's protocol in model.md as 'growable by "
                     f"injection'")


def parse_map_commands(map_text: str) -> set[str]:
    """Command cells from map.md's Commands tables -- the gate commands a unit's
    `Verified by` is supposed to compose from, each already run and confirmed."""
    body = section_text(map_text, r"^commands\b") or map_text
    cmds: set[str] = set()
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set("".join(cells)) <= set("-: "):
            continue
        if cells[0].lower() in {"purpose", "command"}:
            continue
        cmd = cells[1].strip("`* ")
        if cmd and "<" not in cmd:
            cmds.add(" ".join(cmd.split()))
    return cmds


def base_command(sub: str) -> str:
    """A sub-command with its path filters and separators removed, so
    `npm test -- kernel/solver` compares equal to the map's `npm test`."""
    out = []
    skip_next = False
    for tok in sub.split():
        if skip_next:
            skip_next = False
            out.append(tok)
            continue
        if tok == "--":
            continue
        if tok in NAME_FLAGS:
            skip_next = True
            out.append(tok)
            continue
        if not tok.startswith("-") and ("/" in tok or "." in tok) and out:
            continue
        out.append(tok)
    return " ".join(out)


def check_verified_by_provenance(units: dict[str, dict], map_file: Path | None) -> None:
    """Every gate command traces to a command map.md confirmed by running it.

    'Every command must have been run and its output confirmed before this file is
    written: an unverified command in a packet is a blocked agent' -- map.md says
    so about itself; this is the check that it is true.
    """
    if not map_file or not map_file.exists():
        return
    confirmed = parse_map_commands(map_file.read_text(encoding="utf-8",
                                                      errors="replace"))
    if not confirmed:
        warn("map.md has no Commands table with confirmed commands -- a unit's "
             "'Verified by' is supposed to compose from it")
        return
    for uid, u in sorted(units.items()):
        verified = u["fields"].get("Verified by", "").strip().strip("`")
        if not verified:
            continue
        for sub in re.split(r"&&|\|\||;", verified):
            base = base_command(sub.strip())
            if not base:
                continue
            if not any(base in c or c in base for c in confirmed):
                warn(f"{uid}: gate command {base!r} appears in no map.md Commands "
                     f"table -- either it was never confirmed to run, or the map "
                     f"is stale")


def component_test_layout(map_text: str) -> list[tuple[str, str]]:
    """(code path, tests path) per component from map.md's Repository shape table,
    keeping only components whose tests live outside the code tree."""
    body = section_text(map_text, r"repository shape") or map_text
    out: list[tuple[str, str]] = []
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or set("".join(cells)) <= set("-: "):
            continue
        if cells[0].lower() in {"component", "name"}:
            continue
        code, tests = cells[2], cells[3]
        if not code or not tests or "<" in code or "<" in tests:
            continue
        if "co-located" in tests.lower() or "colocated" in tests.lower():
            continue
        if path_covered_by(norm_path(tests), norm_path(code)):
            continue  # tests sit inside the code tree; owning the code owns them
        out.append((code, tests))
    return out


def check_test_ownership(units: dict[str, dict], map_file: Path | None) -> None:
    """`Owns` includes the unit's own test files.

    Only checkable where map.md says tests live somewhere other than beside the
    source. Every unit in the reference build hit this question independently and
    answered it privately, which is the recurrence that motivated the rule.
    """
    if not map_file or not map_file.exists():
        return
    layouts = component_test_layout(map_file.read_text(encoding="utf-8",
                                                       errors="replace"))
    if not layouts:
        return
    for uid, u in sorted(units.items()):
        f = u["fields"]
        if "TEST-" not in f.get("Implements", "") and "TEST-" not in f.get("Done when", ""):
            continue
        owned = split_list(f.get("Owns", ""))
        for code, tests in layouts:
            in_component = any(path_covered_by(p, code) for p in owned)
            owns_tests = any(path_covered_by(p, tests) or path_covered_by(tests, p)
                             for p in owned)
            if in_component and not owns_tests:
                warn(f"{uid}: owns code under {code!r} and cites test ids, but owns "
                     f"nothing under {tests!r}, where map.md says that component's "
                     f"tests live -- a unit cannot write its own evidence")


def check_depth_horizon(units: dict[str, dict]) -> None:
    """Lazy detail is safe only while nothing is built from it.

    The horizon rule promises an economy the validator must not turn into a hole:
    the wave about to be built is fully contracted, and no unit renders into a
    packet below full depth. Everything further out may be sketched, because
    wave 1 routinely invalidates wave 5 anyway.
    """
    waves = []
    for u in units.values():
        try:
            waves.append(float(u["fields"].get("Wave", "0")))
        except ValueError:
            continue
    if not waves:
        return
    nxt = min(waves)
    for uid, u in sorted(units.items()):
        depth = depth_of(u)
        if depth == "full" or depth not in DEPTHS:
            continue
        try:
            wave = float(u["fields"].get("Wave", "0"))
        except ValueError:
            continue
        if wave == nxt:
            err(f"{uid} is in wave {u['fields'].get('Wave')}, the next wave to be "
                f"built, but is only {depth} -- the wave being rendered is fully "
                f"contracted, or its agents are given a contract with holes in it")
        elif depth == "named" and wave <= nxt + 1:
            warn(f"{uid} is one wave out and only named -- sketch it (owns, needs, "
                 f"implements) before the wave before it is rendered")


def check_lock_freshness(plan_dir: Path, plan_text: str) -> None:
    """The lock calls itself the proof that validation passed. It can only be that
    if it says which plan it passed."""
    lock = plan_dir / "plan.lock.yaml"
    if not lock.exists():
        return
    recorded = re.search(r'^plan_hash:\s*"([0-9a-f]+)"',
                         lock.read_text(encoding="utf-8", errors="replace"), re.M)
    if not recorded:
        return
    current = hashlib.sha256(plan_text.encode("utf-8")).hexdigest()[:16]
    if recorded.group(1) != current:
        warn("plan.lock.yaml records a different build-plan.md than the one on disk "
             "-- regenerate it with --write-lock before trusting it")


def depth_of(u: dict) -> str:
    return (u["fields"].get("Depth", "full") or "full").strip().lower()


def check_fields(units: dict[str, dict]) -> None:
    for uid, u in units.items():
        f = u["fields"]
        depth = depth_of(u)
        if depth not in DEPTHS:
            err(f"{uid}: Depth '{depth}' not one of {sorted(DEPTHS)}")
            depth = "full"
        for req in DEPTHS[depth]:
            if req not in f:
                err(f"{uid}: missing required field '{req}'" +
                    ("" if depth == "full" else f" (required even at depth {depth})"))
        ex = f.get("Execution", "").lower()
        if ex and ex not in EXECUTIONS:
            err(f"{uid}: Execution '{ex}' not one of {sorted(EXECUTIONS)}")
        wave = f.get("Wave", "")
        if wave and not re.match(r"^\d+(\.\d+)?$", wave):
            err(f"{uid}: Wave '{wave}' is not numeric")
        if depth != "named" and is_none(f.get("Owns", "")):
            err(f"{uid}: Owns is empty -- every unit must own at least one path")
        # The Done when / Verified by checks below are already guarded on the field
        # being present, so a sketched unit skips them without a special case. The
        # Implements checks run at every depth, because coverage is not lazy.

        # Done-when must be machine-checkable, not prose.
        done = f.get("Done when", "")
        if done and not (ANCHOR_RE.search(done) or "`" in done
                         or re.search(r"\d", done)):
            warn(f"{uid}: 'Done when' looks like prose -- needs a test id, command, "
                 f"or measurable")
        verified = f.get("Verified by", "")
        if verified and "`" not in verified:
            warn(f"{uid}: 'Verified by' should be a runnable command in backticks")
        if not ANCHOR_RE.search(f.get("Implements", "")):
            warn(f"{uid}: Implements cites no spec anchor, decision or test id")
        if "TEST-" not in f.get("Implements", "") and "TEST-" not in done:
            warn(f"{uid}: no test id -- either a spec gap or an untestable unit")


def check_parallel_safety(units: dict[str, dict]) -> None:
    """Ownership and modification are different sets. Asserting disjointness is not
    checking it -- this is the check."""
    waves: dict[str, list[dict]] = {}
    for u in units.values():
        waves.setdefault(u["fields"].get("Wave", "?"), []).append(u)

    for wave, members in sorted(waves.items()):
        for u in members:
            touches = split_list(u["fields"].get("Touches", ""))
            if touches and u["fields"].get("Execution", "").lower() == "parallel-safe":
                err(f"{u['id']}: marked parallel-safe but Touches {touches} -- "
                    f"make it sequential, or defer the wiring to an integration unit")

        for i, a in enumerate(members):
            for b in members[i + 1:]:
                for pa in split_list(a["fields"].get("Owns", "")):
                    for pb in split_list(b["fields"].get("Owns", "")):
                        if glob_conflict(pa, pb):
                            err(f"wave {wave}: {a['id']} and {b['id']} both own "
                                f"overlapping paths ({pa} / {pb})")

        parallel = [u for u in members
                    if u["fields"].get("Execution", "").lower() == "parallel-safe"]
        if len(parallel) > 4:
            warn(f"wave {wave}: {len(parallel)} parallel units -- every merge needs a "
                 f"human, so review capacity is the real cap (4 is the tested maximum)")


def check_graph(units: dict[str, dict]) -> list[str]:
    """Resolve deps, forbid cycles, require dependencies in strictly earlier waves."""
    for uid, u in units.items():
        for dep in split_list(u["fields"].get("Needs", "")):
            if dep not in units:
                err(f"{uid}: Needs '{dep}' which does not exist")
                continue
            try:
                mine = float(u["fields"].get("Wave", "0"))
                theirs = float(units[dep]["fields"].get("Wave", "0"))
            except ValueError:
                continue
            if theirs >= mine:
                err(f"{uid} (wave {mine}) needs {dep} (wave {theirs}) -- dependencies "
                    f"must sit in strictly earlier waves")

    order: list[str] = []
    state: dict[str, int] = {}

    def visit(uid: str, trail: list[str]) -> None:
        if state.get(uid) == 2:
            return
        if state.get(uid) == 1:
            err(f"dependency cycle: {' -> '.join(trail + [uid])} -- break it by "
                f"extracting a shared contract unit both sides depend on")
            return
        state[uid] = 1
        for dep in split_list(units[uid]["fields"].get("Needs", "")):
            if dep in units:
                visit(dep, trail + [uid])
        state[uid] = 2
        order.append(uid)

    for uid in units:
        visit(uid, [])
    return order


def check_coverage(units: dict[str, dict], spec: Path | None) -> list[str]:
    if not spec or not spec.exists():
        return []
    text = spec.read_text(encoding="utf-8", errors="replace")
    spec_tests = set(re.findall(r"TEST-[A-Z0-9]+-\d+", text))
    claimed: set[str] = set()
    for u in units.values():
        for field in ("Implements", "Done when"):
            claimed.update(re.findall(r"TEST-[A-Z0-9]+-\d+",
                                      u["fields"].get(field, "")))
    unassigned = sorted(spec_tests - claimed)
    for t in unassigned:
        warn(f"coverage: {t} is in the spec but assigned to no unit")
    for t in sorted(claimed - spec_tests):
        err(f"coverage: {t} is claimed by a unit but does not exist in the spec")
    return unassigned


# ---------------------------------------------------------------------------
# Concept coverage: reduction must not lose a requirement silently.
#
# Two questions, one extractor. Did every concept the spec names reach the model
# (as core or as derived)? And did it reach a build unit? The session that
# motivated these found five modules with no owning unit at all, one of which
# had already shipped broken for want of exactly this check.
# ---------------------------------------------------------------------------

def norm_tokens(name: str) -> list[str]:
    s = re.sub(r"^[\d.)\s]+", "", name.lower()).replace("`", " ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [t for t in s.split() if t not in NOISE_TOKENS]


def head_phrase(name: str) -> str:
    """The leading noun phrase of a concept name, dropping a parenthetical gloss or
    a trailing qualification. "apply (the operator core, including the Coherence
    gate)" requires all six of its significant tokens to appear in one unit's
    fields otherwise, which reported a false orphan for a plainly owned concept --
    15 of 21 residual warnings in the reference run were this."""
    head = re.split(r"\s*[(\[]|\s+[-–—:]\s+", name)[0].strip()
    return head if norm_tokens(head) else name


def norm_hay(text: str) -> str:
    return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split()) + " "


def concept_in(tokens: list[str], hay: str) -> bool:
    """Every significant token of the name must appear as a whole word, with a
    one-character plural tolerance. Conservative on purpose: a false 'dropped
    concept' teaches people to ignore the check."""
    if not tokens:
        return True
    for t in tokens:
        forms = {t, t + "s"}
        if len(t) > 3 and t.endswith("s"):
            forms.add(t[:-1])
        if not any(f" {f} " in hay for f in forms):
            return False
    return True


def section_text(text: str, title_re: str) -> str:
    """A heading's body, up to the next heading at the same or a higher level."""
    heads = list(HEADING_RE.finditer(text))
    for i, h in enumerate(heads):
        if not re.search(title_re, h.group(2), re.I):
            continue
        level = len(h.group(1))
        end = len(text)
        for nxt in heads[i + 1:]:
            if len(nxt.group(1)) <= level:
                end = nxt.start()
                break
        return text[h.end():end]
    return ""


def extract_spec_concepts(text: str) -> list[str]:
    """Names the spec presents as things to build. Precision over recall."""
    heads = [(len(m.group(1)), m.group(2).strip(), m.start())
             for m in HEADING_RE.finditer(text)]
    found: list[str] = []

    for i, (level, title, _) in enumerate(heads):
        prefixed = PREFIXED_RE.match(title)
        if prefixed:
            found.append(prefixed.group(1).strip())
            continue
        # A collective heading, but only if it actually has children to collect;
        # otherwise "## Modules" in a table of contents would emit nothing useful.
        stripped = re.sub(r"^[\d.)\s]+", "", title)
        if not COLLECTIVE_RE.search(stripped) or len(norm_tokens(stripped)) > 3:
            continue
        children = []
        for child_level, child_title, _ in heads[i + 1:]:
            if child_level <= level:
                break
            if child_level == level + 1:
                children.append(child_title)
        if len(children) >= 2:
            found.extend(children)

    seen: set[str] = set()
    out: list[str] = []
    for name in found:
        clean = re.sub(r"^[\d.)\s]+", "", name).strip().strip("`*_")
        if BOILERPLATE_RE.match(clean) or not norm_tokens(clean):
            continue
        key = " ".join(norm_tokens(clean))
        if key not in seen:
            seen.add(key)
            out.append(clean)
    return out


def check_spec_build_order(text: str) -> None:
    m = SPEC_ORDER_RE.search(text)
    if m:
        warn(f"the spec carries its own build ordering ({m.group(0).strip()!r}) -- "
             f"validate it against these same checks rather than treating it as an "
             f"input (Stage 0)")


def check_core_coverage(concepts: list[str], model: Path | None) -> list[str]:
    """Every spec concept lands in the generative core or the derived table.
    Anything in neither was dropped by reduction."""
    if not concepts or not model or not model.exists():
        return []
    text = model.read_text(encoding="utf-8", errors="replace")
    core = section_text(text, r"generative core|irreducible core")
    derived = section_text(text, r"derived from the core|^derived\b")
    if not core.strip() and not derived.strip():
        warn("model.md has no 'Generative core' or 'Derived from the core' section "
             "-- the reduction pass has not been recorded, so nothing can check it")
        return []
    hay = norm_hay(core + "\n" + derived)
    unmodelled = [c for c in concepts
                  if not concept_in(norm_tokens(head_phrase(c)), hay)]
    for c in unmodelled:
        coverage_finding(f"core coverage: spec concept {c!r} is in neither the "
                         f"generative core nor the derived table -- reduction dropped it")
    return unmodelled


def check_concept_owners(concepts: list[str],
                         units: dict[str, dict]) -> dict[str, list[str]]:
    """Every spec concept has an owning unit. Multiple matches are recorded in the
    lock rather than warned: short concept names over-match, and path-level
    single-ownership is already enforced by check_parallel_safety."""
    owners: dict[str, list[str]] = {}
    for c in concepts:
        tokens = norm_tokens(head_phrase(c))
        hits = []
        for uid, u in units.items():
            f = u["fields"]
            hay = norm_hay(" ".join([u["name"], f.get("Owns", ""),
                                     f.get("Implements", ""), f.get("Done when", "")]))
            if concept_in(tokens, hay):
                hits.append(uid)
        owners[c] = sorted(hits)
        if not hits:
            coverage_finding(f"orphan: spec concept {c!r} has no owning unit -- it is "
                             f"named in no unit's name, Owns, Implements or Done when")
    return owners


def check_tracer_negatives(text: str) -> None:
    """A mechanism only ever exercised on its success path has been shown to be
    callable, not to work. Advisory: 'mechanism' is not mechanically enumerable,
    so the plan declares them and this checks the declaration."""
    m = TRACER_SECTION_RE.search(text)
    if not m:
        warn("no 'Mechanisms in the tracer' section -- the tracer's rejection paths "
             "are unchecked, and a gate that never rejects anything is a stub")
        return
    body = text[m.end():]
    nxt = re.search(r"^#{1,6}\s", body, re.M)
    if nxt:
        body = body[:nxt.start()]
    rows = 0
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or set("".join(cells)) <= set("-: "):
            continue
        mech, negative = cells[0], cells[2]
        if not mech or mech.lower() in {"mechanism"} or "XXX" in "".join(cells):
            continue
        rows += 1
        justified = (ANCHOR_RE.search(negative) or "`" in negative
                     or (negative.startswith("(") and len(negative) >= 20))
        if not justified:
            warn(f"tracer: mechanism {mech!r} names no rejection path -- give the test "
                 f"id that exercises its negative branch, or a parenthesised reason "
                 f"why it has none")
    if not rows:
        warn("the 'Mechanisms in the tracer' table is empty -- list every mechanism "
             "the first increment ships and how each one is seen to refuse something")


def emit_lock(units: dict[str, dict], order: list[str], spec: Path | None,
              unassigned: list[str], out: Path,
              concepts: list[str] | None = None,
              unmodelled: list[str] | None = None,
              owners: dict[str, list[str]] | None = None,
              plan_text: str = "") -> None:
    def q(s: str) -> str:
        return '"' + s.replace('"', '\\"') + '"'

    lines = [
        "# Generated by validate_plan.py. Never hand-edit.",
        "# Its existence is the proof that validation passed.",
        "version: 1",
    ]
    # The fifth pin. The four echoed into results/ answer "was the world
    # different when this unit was built"; this one answers "and what checked
    # the plan it was built from", which is the question the lock's own
    # docstring implies it can already answer.
    lines.append(f"validator_version: {q(VERSION)}")
    try:
        lines.append("validator_hash: " +
                     q(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]))
    except OSError:
        pass
    if plan_text:
        lines.append("plan_hash: " +
                     q(hashlib.sha256(plan_text.encode("utf-8")).hexdigest()[:16]))
    if spec and spec.exists():
        digest = hashlib.sha256(spec.read_bytes()).hexdigest()[:16]
        lines += [f"spec_path: {q(str(spec))}", f"spec_hash: {q(digest)}"]
    lines.append(f"unit_count: {len(units)}")
    lines.append("topological_order: [" +
                 ", ".join(q(u) for u in order) + "]")

    waves: dict[str, list[str]] = {}
    for uid, u in units.items():
        waves.setdefault(u["fields"].get("Wave", "?"), []).append(uid)
    lines.append("waves:")
    for wave in sorted(waves, key=lambda w: float(w) if w != "?" else 1e9):
        lines.append(f"  {q(wave)}:")
        lines.append("    units: [" + ", ".join(q(u) for u in sorted(waves[wave])) + "]")

    lines.append("units:")
    for uid in sorted(units):
        f = units[uid]["fields"]
        lines.append(f"  {uid}:")
        lines.append(f"    name: {q(units[uid]['name'])}")
        lines.append(f"    wave: {q(f.get('Wave', ''))}")
        # Rendering reads this: a packet may only be built from a full contract.
        lines.append(f"    depth: {q(depth_of(units[uid]))}")
        lines.append(f"    execution: {q(f.get('Execution', ''))}")
        lines.append("    owns: [" +
                     ", ".join(q(p) for p in split_list(f.get("Owns", ""))) + "]")
        lines.append("    touches: [" +
                     ", ".join(q(p) for p in split_list(f.get("Touches", ""))) + "]")
        lines.append("    needs: [" +
                     ", ".join(q(p) for p in split_list(f.get("Needs", ""))) + "]")
        lines.append(f"    verified_by: {q(f.get('Verified by', ''))}")

    lines.append("coverage:")
    lines.append("  unassigned_tests: [" + ", ".join(q(t) for t in unassigned) + "]")

    if concepts:
        owners = owners or {}
        unmodelled = unmodelled or []
        lines.append("concept_coverage:")
        lines.append(f"  spec_concepts: {len(concepts)}")
        lines.append("  not_in_model: [" + ", ".join(q(c) for c in unmodelled) + "]")
        lines.append("  unowned: [" +
                     ", ".join(q(c) for c in concepts if not owners.get(c)) + "]")
        lines.append("  owners:")
        for c in concepts:
            lines.append(f"    {q(c)}: [" +
                         ", ".join(q(u) for u in owners.get(c, [])) + "]")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan_dir", type=Path)
    ap.add_argument("--spec", type=Path, default=None)
    ap.add_argument("--model", type=Path, default=None)
    ap.add_argument("--map", dest="map_file", type=Path, default=None)
    ap.add_argument("--write-lock", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="promote concept-coverage warnings to errors")
    args = ap.parse_args()

    global STRICT
    STRICT = args.strict

    plan = args.plan_dir / "build-plan.md"
    if not plan.exists():
        print(f"ERROR no build-plan.md in {args.plan_dir}")
        return 1

    plan_text = plan.read_text(encoding="utf-8", errors="replace")
    units = parse_units(plan_text)
    if not units:
        print("ERROR no units found -- expected headings like '## U-001: name'")
        return 1

    model = args.model
    if model is None and (args.plan_dir / "model.md").exists():
        model = args.plan_dir / "model.md"
    map_file = args.map_file
    if map_file is None and (args.plan_dir / "map.md").exists():
        map_file = args.plan_dir / "map.md"

    check_fields(units)
    check_parallel_safety(units)
    order = check_graph(units)
    unassigned = check_coverage(units, args.spec)
    check_tracer_negatives(plan_text)

    # Closure and cross-document agreement: the same fact, asserted in more than
    # one place, reconciled by nobody until here.
    check_verified_by_coverage(units)
    check_depth_horizon(units)
    check_global_ownership(units)
    check_cross_document_ownership(units, model)
    check_cross_wave_touches(units, model)
    check_verified_by_provenance(units, map_file)
    check_test_ownership(units, map_file)
    check_lock_freshness(args.plan_dir, plan_text)

    concepts: list[str] = []
    unmodelled: list[str] = []
    owners: dict[str, list[str]] = {}
    if args.spec and args.spec.exists():
        spec_text = args.spec.read_text(encoding="utf-8", errors="replace")
        check_spec_build_order(spec_text)
        concepts = extract_spec_concepts(spec_text)
        if concepts:
            unmodelled = check_core_coverage(concepts, model)
            owners = check_concept_owners(concepts, units)
        else:
            print("NOTE  no module/component section found in the spec -- concept "
                  "coverage skipped; list the concepts in model.md by hand")

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")

    if args.write_lock and not errors:
        lock = args.plan_dir / "plan.lock.yaml"
        emit_lock(units, order, args.spec, unassigned, lock,
                  concepts, unmodelled, owners, plan_text)
        print(f"\nwrote {lock}")
    elif args.write_lock:
        print("\nlock not written: fix errors first")

    summary = f"\n{len(units)} units checked"
    if concepts:
        summary += (f", {len(concepts)} spec concepts "
                    f"({len(concepts) - len(unmodelled)} modelled, "
                    f"{sum(1 for c in concepts if owners.get(c))} owned)")
    print(f"{summary}: {len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
