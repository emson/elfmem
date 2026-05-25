# 0005 — Peer-protocol hardening: surgical fixes over architectural rewrite

**Status**: Accepted — implemented on branch `peer-protocol-refactor`
**Date**: 2026-05-25
**Deciders**: elf (curator), Ben

## Context

Four bugs surfaced when elf attempted to reply to a configured-but-unregistered peer (Alv):

1. `peers:` declared in `.elfmem/config.yaml` was silently dropped — `ElfmemConfig` had no `peers` field, so pydantic ignored the section. `peer_list()` returned empty despite the YAML declaration.
2. Outbox slug derivation drifted from inbox slug derivation. `peer_send("Alv", …)` produced `outbox/alv/`; `peer_send("elf:alv", …)` produced `outbox/elf-alv/`. Same recipient, two folders.
3. `_write_message_file` claimed atomicity but used direct `path.write_text`. Concurrent scanners could observe half-written envelopes; duplicate sends silently overwrote.
4. `peer_send` to a peer whose `delivery_path` pointed at an unmounted or uninitialised directory wrote to a black hole — no error, no audit, just lost messages.

Twenty stress-test scenarios on the proposed design (see prior conversation) exposed a much larger refactor surface: a `PeerDID` value object, an envelope-schema rewrite with `schema_version` and time-bucketed `msg_id`, quarantine routing for unknown senders and corrupt envelopes, full filesystem migration. ~500+ LOC across every peer code path.

A choice was needed: ship the four-bug surgical fix, or the full architectural cleanup.

## Alternatives considered

1. **Surgical fix (~150 LOC)**: config sync + slug canonicalisation + atomic write + recipient precondition + legacy folder migration. Bugs fixed, no envelope-schema break, no DB schema migration.
2. **Surgical + envelope hardening (~300 LOC)**: surgical + `schema_version` field + time-bucketed `msg_id` + quarantine paths. Forward-compatible envelope, but breaks wire format with v0.18 peers and requires receiver migration.
3. **Full iterated design (~500+ LOC)**: option 2 + `PeerDID` dataclass threaded through every signature + complete inbox-scanner rewrite + multiple quarantine reasons. High blast radius, multi-PR safety required.

## Decision

**Ship option 1.** Phases 1, 2, 3, 4, 7, 8 from the iterated design. Defer phases 5 (envelope `schema_version` + time-bucketed `msg_id`) and 6 (quarantine routing) to a follow-up PR.

Load-bearing choices inside option 1:

- **`peer_roster` remains the canonical registry; config syncs into it.** Not the other way around. Insert-only: declared trust applies at first sync; subsequent runtime adjustments via `peer_trust` are never overwritten. The operator declares peers in `config.yaml`; operational state belongs to the database.
- **DIDs are strings, not a value object.** A `PeerDID` dataclass would have to be threaded through every public and private signature touching a recipient — large churn for marginal type-safety. Instead, one `canonical_did(conn, to_peer)` helper resolves any caller input (DID, display name, mixed case) to the canonical form before slug derivation. Strings everywhere, one canonicalisation function.
- **Atomic write via dotfile temp + `os.rename`.** Idempotent: if the destination already exists (same content-hashed `msg_id`), `_write_envelope_atomic` returns `False` and the existing path is reused. Aligns the on-disk behaviour with the content-addressable `msg_id` design.
- **Recipient-readiness is a hard fail.** Soft-degrading to local-outbox would mask configuration drift. `PeerError` with the exact `elfmem init` invocation in `.recovery` lets the agent fix and retry.
- **Legacy folder migration is auto-apply, refuse-on-conflict.** Single-folder drift (`outbox/alv/` only) is renamed silently with INFO log; both-folder drift (`outbox/alv/` *and* `outbox/elf-alv/`) is refused with a `.recovery` instructing manual merge. Silent merge could lose audit history.

## What this defers (and why)

- **Phase 5 — `Envelope.schema_version` + time-bucketed `msg_id`.** The current `msg_id = f"m_{hash(content)[:8]}"` collapses two legitimate "ping" sends from the same sender on the same day into one. Real bug *only* under high-frequency repeat-content scenarios; peer messaging today is conversational, not telemetric. No production complaint. Defer.
- **Phase 6 — Quarantine routing.** Unknown senders currently land in their named subdirectory like any other; malformed envelopes fail silently in `_parse_message`. Refactor would route both to `inbox/.unknown/<did>/` and `inbox/.quarantine/<reason>/` for operator review. Useful for hardening federation at 10k+ users (per the cloud-architecture sketch); not load-bearing for the elf↔Alv use case today. Defer.

## Why surgical wins under our axioms

| Axiom | How option 1 honours it |
|---|---|
| Minimum earned change | Every line of code closes a specific named bug. No speculative refactor. |
| Single source of truth | One slugifier (`_slugify`), one canonicaliser (`canonical_did`), one atomic writer (`_write_envelope_atomic`). |
| Fail fast | `PeerError` at every boundary that can fail: malformed DID, unknown peer, uninitialised recipient. |
| No defensive code | Recipient-readiness raises rather than silently falling back. |
| Agent-first contract | Every new exception carries `.recovery`; the `peer_send` guide entry now documents the new failure mode. |
| SIMPLE · ELEGANT | No new dataclass. No new envelope schema. No new quarantine machinery. Six small functions added; one bug-fix per. |

## Trigger conditions to revisit deferred phases

- **Reopen phase 5** if production traffic includes legitimate repeat-content sends (e.g., periodic ping or heartbeat envelopes) and msg_id collisions are observed.
- **Reopen phase 6** if the peer roster grows beyond ~10 entries, *or* if we federate to a product-elf at scale where unknown-sender volume justifies systematic quarantine handling. The cloud-architecture sketch (note pending peer-send to Alv) is the natural forcing function.

## Test surface

115 peer-area tests pass (81 baseline + 34 new). Full suite green at 1123. No DB schema migration; `CURRENT_SCHEMA_VERSION` unchanged at 5.

## References

- Branch: `peer-protocol-refactor`, commit `502f90c`
- Stored note to Alv (block `b49b58048714154a`): cloud-architecture reasoning that motivated the federation primitives this refactor now makes robust.
