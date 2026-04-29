# Agent Communication v1 — Peer Knowledge Exchange

**Status:** Design complete — ready for review
**Date:** 2026-04-29
**Author:** elf (with Ben)
**Depends on:** All existing infrastructure (blocks, frames, edges, mind, outcome, curate)

---

## 1. What We're Building

elfmem today is a solitary mind: one process, one SQLite database, one identity. This plan adds the ability for elfmem instances to **exchange knowledge, ask questions, and learn from each other** — while preserving the zero-infrastructure, single-file, pull-based architecture that makes elfmem what it is.

Three real demands drive this:

| Demand | Nature | Example |
|--------|--------|---------|
| **Self-federation** | Replication | Ben's laptop-elf and server-elf should share knowledge without manual copy |
| **Specialisation** | Consultation | A research-elf and a trading-elf ask each other questions |
| **Inter-personhood** | Negotiation | Ben's elf talks to another person's elf, with trust boundaries |

### What this is NOT

- Not a distributed system (no servers, no endpoints, no always-on processes)
- Not a chat protocol (asynchronous, pull-based, file-mediated)
- Not a framework (no orchestration, no routing, no pub-sub)

elfmem remains a **passive memory system**. It stores, retrieves, scores, and signs. The agent process (Claude Code, a script, a cron job) decides what to send, when to check, and how to respond.

---

## 2. Design Principles

### 2.1 Constitutional alignment

Every design decision was tested against elf's constitutional blocks:

| Axiom | How this plan respects it |
|-------|--------------------------|
| **Zero infrastructure** | No servers, no listeners, no external processes. Export = write a file. Import = read a file. Transport is not elfmem's problem. |
| **Three rhythms** | Import = Heartbeat (learn). Trust update = Breathing (outcome). Export = Sleep (curate-adjacent). No new rhythm. |
| **Agent-first contract** | Every operation returns typed results with `__str__`, `summary`, `to_dict()`. Exceptions carry `.recovery`. |
| **Everything is a block** | Messages, lessons, peer metadata — all blocks. No new storage primitives. |
| **SQLite, single-file** | Three schema additions to existing `blocks` table. One new `peer_roster` table. Same database. |

### 2.2 Protocol axioms (from research document, filtered for v1)

| # | Axiom | v1 status |
|---|-------|-----------|
| 1 | **Sovereignty first** — each agent owns its blocks; federation is copies + provenance, never shared write | Core |
| 2 | **Asymmetric trust** — A can trust B while B distrusts A; no handshakes required | Core |
| 3 | **Outcomes are ground truth** — trust is driven by outcome closure on imported blocks, not by agreement | Core |
| 4 | **Privacy by choice** — `share` field defaults to `private`; sharing is opt-in | Core |
| 5 | **No central gatekeeper** — peers are registered manually; no registry, no discovery service | Core |
| 6 | **Unidirectional learning** — B learns from A's message without A knowing | Core |

Axioms 7–10 from the research document (mesh resilience, disagreement improves truth, permanent redaction, verifiability via signing) are deferred to v2.

### 2.3 Sending mirrors receiving

The key architectural insight: **peer_send() is the mirror of learn()**. Both are heartbeat-speed, no LLM, pure write.

```
Receiving:  learn()       → inbound block enters inbox     (heartbeat)
Sending:    peer_send()   → outbound block enters inbox    (heartbeat)
                          + writes to outbox directory      (heartbeat)
```

The outbound message is simultaneously:
- **Stored** in the sender's memory (enters inbox, later promoted by dream())
- **Written** to an outbox directory as a signed JSON file

The existing pipeline handles the rest:
- `dream()` embeds the message, creates edges, promotes to active
- `curate()` decays old messages naturally

Transport (moving outbox files to the peer's inbox directory) is **not elfmem's concern**. rsync, shared folder, HTTP POST via a one-liner adapter, manual copy — all work.

---

## 3. Schema Changes

### 3.1 New columns on `blocks` table

```python
# In db/models.py — three new nullable columns
Column("source_peer", Text),        # DID of originating peer (None = local)
Column("share", Text),              # private | public | peer (default: private)
Column("envelope_json", Text),      # JSON envelope for message blocks (see §3.3)
```

**Why these three:**
- `source_peer` — provenance tracking. Which peer did this block come from? None means locally created. Immutable once set.
- `share` — privacy gate. Controls what gets included in exports. Default `private` means zero behavior change until explicit opt-in. Blocks tagged `self/*` are always `private` (enforced in export, not schema).
- `envelope_json` — message metadata for `category=message` blocks only. Stores `{from, to, in_reply_to, msg_id, direction, sent_at}`. JSON rather than separate columns because only message blocks use it; avoids sparse columns on every block.

### 3.2 New table: `peer_roster`

```python
peer_roster = Table(
    "peer_roster",
    metadata,
    Column("did", Text, primary_key=True),        # Peer identifier (see §4)
    Column("name", Text, nullable=False),          # Human-readable name
    Column("public_key", Text),                    # Ed25519 public key (v2, nullable for now)
    Column("trust", Float, nullable=False, default=0.0),  # Global trust [0.0, 1.0]
    Column("is_self", Integer, nullable=False, default=0), # 1 = same identity, diff machine
    Column("first_contact", Text, nullable=False), # ISO timestamp
    Column("last_active", Text, nullable=False),   # ISO timestamp of last exchange
    Column("blocks_imported", Integer, nullable=False, default=0),
    Column("blocks_exported", Integer, nullable=False, default=0),
    Column("messages_in", Integer, nullable=False, default=0),
    Column("messages_out", Integer, nullable=False, default=0),
    Column("notes", Text),                         # Free-form agent notes about this peer
)
```

**Key field: `is_self`** — distinguishes self-federation (same identity, different machine, trust=1.0) from peer-federation (different identity, trust earned). This replaces the need for a separate sync mechanism.

### 3.3 Message envelope schema

For `category=message` blocks, `envelope_json` stores:

```json
{
  "msg_id": "m_a1b2c3d4",
  "direction": "outbound",
  "from_did": "elf:laptop-elf",
  "to_did": "elf:trader-elf",
  "in_reply_to": null,
  "sent_at": "2026-04-29T14:30:00Z"
}
```

`msg_id` is generated as `m_` + first 8 chars of content hash. `in_reply_to` chains create conversation graphs — traversable via standard edge queries.

### 3.4 New index

```python
Index("idx_blocks_source_peer", blocks.c.source_peer)
```

Enables efficient filtering of peer-originated blocks for trust scoring and audit.

### 3.5 Migration strategy

elfmem has no migration framework (no Alembic). Schema changes use `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ADD COLUMN` with `IF NOT EXISTS` guards in a version-checked init function:

```python
async def _migrate_peer_schema(conn: AsyncConnection) -> None:
    """Add peer communication columns if missing (idempotent)."""
    # Check schema version
    version = await get_config(conn, "schema_version")
    if version and int(version) >= 2:
        return

    # Add columns (SQLite ALTER TABLE ADD COLUMN is always append)
    for col, typ, default in [
        ("source_peer", "TEXT", None),
        ("share", "TEXT", "'private'"),
        ("envelope_json", "TEXT", None),
    ]:
        try:
            await conn.execute(text(
                f"ALTER TABLE blocks ADD COLUMN {col} {typ}"
                + (f" DEFAULT {default}" if default else "")
            ))
        except OperationalError:
            pass  # Column already exists

    # Create peer_roster table
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS peer_roster (
            did TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            public_key TEXT,
            trust REAL NOT NULL DEFAULT 0.0,
            is_self INTEGER NOT NULL DEFAULT 0,
            first_contact TEXT NOT NULL,
            last_active TEXT NOT NULL,
            blocks_imported INTEGER NOT NULL DEFAULT 0,
            blocks_exported INTEGER NOT NULL DEFAULT 0,
            messages_in INTEGER NOT NULL DEFAULT 0,
            messages_out INTEGER NOT NULL DEFAULT 0,
            notes TEXT
        )
    """))

    await set_config(conn, "schema_version", "2")
```

Called from `MemorySystem.__init__` or `from_config()`. Idempotent.

---

## 4. Identity Model

### 4.1 DID format (v1: simple, local)

v1 uses a simple `elf:<name>` format — not a full DID spec, but designed to be upgradeable to `did:web` or `did:key` in v2.

```
elf:laptop-elf
elf:server-elf
elf:research-elf
elf:trader-elf
```

Generated during `elfmem peer init` and stored in config:

```yaml
# .elfmem/config.yaml
peer:
  identity: "elf:laptop-elf"
  # public_key and private_key added in v2 (signing)
```

### 4.2 Why not Ed25519 signing in v1?

Signing adds: a crypto dependency (PyNaCl or cryptography), key management (generation, rotation, backup), and signature verification on every import. This is real complexity for a problem that doesn't exist yet — in v1, the user manually runs `elfmem import` and knows where the file came from. Trust is contextual, not cryptographic.

**Decision:** Defer signing to v2 when automated transport makes provenance verification necessary. v1 tracks `source_peer` for provenance but does not verify it cryptographically.

### 4.3 Peer registration

Peers are registered manually. No discovery, no mDNS, no registry.

```bash
elfmem peer add elf:trader-elf --name "Trading Elf"
elfmem peer add elf:server-elf --name "Server Elf" --self  # same identity
```

The `--self` flag sets `is_self=1` and `trust=1.0`. Self-peers are trusted completely — their blocks are imported at full confidence, edges are merged, and dedup runs normally.

---

## 5. Core Operations

### 5.1 Export — `elfmem export`

Produces a JSON bundle of shareable blocks.

```bash
# Export all public blocks
elfmem export --share public -o bundle.json

# Export blocks for a specific peer
elfmem export --share peer --to elf:trader-elf -o bundle.json

# Self-federation: export everything (for your other machine)
elfmem export --all --self -o sync.json
```

**Implementation:**

```python
async def export_blocks(
    conn: AsyncConnection,
    *,
    share_level: str = "public",           # "public" | "peer" | "all"
    to_peer: str | None = None,            # filter for peer-level sharing
    min_confidence: float = 0.0,           # only export blocks above this
    include_edges: bool = True,            # include edges between exported blocks
    identity: str,                         # this instance's DID
) -> ExportBundle:
```

**ExportBundle format:**

```json
{
  "version": 1,
  "exported_at": "2026-04-29T14:30:00Z",
  "from_did": "elf:laptop-elf",
  "block_count": 42,
  "blocks": [
    {
      "id": "a1b2c3d4e5f6g7h8",
      "content": "UK gilts likely to retest 130.0 this week",
      "category": "knowledge",
      "tags": ["trading", "gilts"],
      "confidence": 0.72,
      "created_at": "2026-04-25T10:00:00Z",
      "share": "public"
    }
  ],
  "edges": [
    {
      "from_id": "a1b2c3d4e5f6g7h8",
      "to_id": "f8g7h6i5j4k3l2m1",
      "relation_type": "supports",
      "weight": 0.75
    }
  ]
}
```

**What is exported:**
- Blocks with `share` matching the requested level, status `active` only
- Tags (full list per block)
- Confidence score (the exporter's current confidence — the receiver uses it as a signal but doesn't adopt it directly)
- Edges between exported blocks (relation, weight — not reinforcement counts or internal metadata)

**What is NOT exported:**
- Blocks with `share=private` (default — everything unless explicitly marked)
- Blocks tagged `self/*` (identity blocks never leave the instance)
- Embeddings (receiver re-embeds with its own model)
- Internal metadata (reinforcement_count, decay_lambda, self_alignment, outcome_evidence)
- Session data, system config, co-retrieval staging

**Privacy rule:** `self/*` tags are hard-blocked from export regardless of `share` field. This is enforced in the export function, not by convention. Constitutional blocks are private by definition.

### 5.2 Import — `elfmem import`

Ingests a bundle with provenance tracking.

```bash
# Import from a peer (blocks enter inbox, trust-gated)
elfmem import bundle.json --from elf:trader-elf

# Self-federation (blocks enter inbox at full confidence)
elfmem import sync.json --self-merge
```

**Implementation:**

```python
async def import_blocks(
    conn: AsyncConnection,
    *,
    bundle: ExportBundle,
    from_peer: str,                      # source DID
    is_self_merge: bool = False,         # same identity, different machine
    confidence_floor: float = 0.3,       # starting confidence for peer blocks
) -> ImportResult:
```

**Import logic:**

```
For each block in bundle:
  1. Compute content hash
  2. Check for existing block with same hash:
     - Exists in inbox → skip (duplicate)
     - Exists in active → skip (already known) BUT record edge if not present
     - Exists in archived → re-learn with new ID (may be re-evaluated)
     - Does not exist → create new block
  3. Set fields on new block:
     - status = "inbox" (always — goes through normal dream() pipeline)
     - source_peer = from_peer
     - share = "private" (imported blocks are private by default on this side)
     - category = original category (preserve)
     - tags = original tags + "peer/<from_peer>" (provenance tag)
     - confidence:
       - is_self_merge → preserve original confidence
       - peer trust >= auto_ingest_threshold → confidence_floor × 1.5
       - else → confidence_floor (0.3)
  4. After all blocks imported:
     - Import edges between blocks that both exist on this side
     - Update peer_roster stats (blocks_imported += count)
     - Update peer_roster.last_active
```

**Key property: imported blocks enter `status=inbox`.** They go through the normal `dream()` pipeline — embedding, dedup, contradiction detection, edge creation, promotion. The existing machinery handles them. No special pipeline for peer content.

**What dream() does differently with peer blocks:**
- **Dedup:** If an imported block is near-duplicate of an existing active block, the existing block wins (supersede). The imported block is archived as `archive_reason=superseded`. This prevents peer content from overwriting local knowledge.
- **Contradiction detection:** If an imported block contradicts an existing block, the contradiction is recorded normally. Both blocks survive — the agent resolves contradictions through its own reasoning.
- **Edge creation:** Edges between imported blocks and existing blocks are created normally via cosine similarity. The `source_peer` field is preserved but doesn't affect edge scoring.
- **Message blocks:** Blocks with `category=message` skip dedup (messages are events, not claims). They are always promoted.

### 5.3 Peer Send — `elfmem peer send`

Composes and sends a message to a peer.

```bash
elfmem peer send elf:trader-elf "What is your view on UK gilts this week?"
elfmem peer send elf:trader-elf "I think you're right about SONIA" --reply-to m_a1b2c3d4
```

**Implementation:**

```python
async def peer_send(
    conn: AsyncConnection,
    *,
    to_peer: str,                        # target DID
    content: str,                        # message body
    in_reply_to: str | None = None,      # msg_id of prior message
    identity: str,                       # this instance's DID
    outbox_dir: Path,                    # where to write outbox files
) -> PeerSendResult:
```

**What happens (two simultaneous writes):**

```
1. Create message block via learn():
   - content = message text
   - category = "message"
   - tags = ["peer/outbound", "peer/to:<did>"]
   - source = "peer_send"
   - envelope_json = {msg_id, direction="outbound", from_did, to_did, in_reply_to, sent_at}

2. Write outbox file:
   - Path: <outbox_dir>/<to_peer_slug>/msg_<msg_id>.json
   - Contains: full message block as JSON (content, envelope, tags)

3. If in_reply_to is set:
   - Find the inbound message block with that msg_id
   - Create "replies_to" edge: outbound_block → inbound_block (weight=0.8, origin="agent")

4. Update peer_roster:
   - messages_out += 1
   - last_active = now

5. Return PeerSendResult(block_id, msg_id, to=to_peer, outbox_path)
```

**The message block then follows the normal pipeline:**
- `dream()` embeds it, creates edges to related knowledge blocks, promotes to active
- The message is now searchable — "what did I say to trader-elf about gilts?" works via recall
- `curate()` decays it naturally — old messages fade like any other block

**Outbox directory structure:**

```
~/.elfmem/outbox/
  elf-trader-elf/
    msg_m_a1b2c3d4.json
    msg_m_e5f6g7h8.json
  elf-research-elf/
    msg_m_i9j0k1l2.json
```

### 5.4 Peer Inbox — `elfmem peer inbox`

Checks for and imports inbound messages.

```bash
# List pending messages
elfmem peer inbox

# Import messages from a specific peer
elfmem peer inbox --from elf:trader-elf

# Import all pending messages
elfmem peer inbox --import-all
```

**Implementation:**

```python
async def peer_check_inbox(
    conn: AsyncConnection,
    *,
    inbox_dir: Path,                     # where inbound messages land
    from_peer: str | None = None,        # filter by peer
    import_messages: bool = False,        # True = import, False = list only
    identity: str,                       # this instance's DID
) -> PeerInboxResult:
```

**What happens:**

```
1. Scan inbox directory for .json files:
   - <inbox_dir>/<peer_slug>/msg_*.json

2. For each message file:
   - Parse JSON, extract envelope
   - Validate: to_did matches this instance's identity
   - Check: msg_id not already in database (dedup)

3. If import_messages:
   - For each new message:
     a. Create block via learn():
        - content = message body
        - category = "message"
        - tags = ["peer/inbound", "peer/from:<did>"]
        - source = "peer_inbox"
        - source_peer = from_did
        - envelope_json = {msg_id, direction="inbound", from_did, to_did, in_reply_to, sent_at}
     b. If in_reply_to: create "replies_to" edge to the original outbound message
     c. Update peer_roster: messages_in += 1, last_active = now
     d. Move file to <inbox_dir>/processed/ (don't delete — audit trail)

4. Return PeerInboxResult(messages_found, messages_imported, messages_skipped)
```

**Inbox directory structure:**

```
~/.elfmem/inbox/
  elf-trader-elf/
    msg_m_x1y2z3w4.json        # pending
  processed/
    msg_m_x1y2z3w4.json        # already imported
```

### 5.5 Trust — `elfmem peer trust`

View and manage trust scores.

```bash
# Show trust for a peer
elfmem peer trust elf:trader-elf

# Manually set trust (override)
elfmem peer trust elf:trader-elf --set 0.6

# Show all peers with trust scores
elfmem peer list
```

**Trust model (v1: global scalar, outcome-driven):**

Trust is a single float in `[0.0, 1.0]` per peer, stored in `peer_roster.trust`.

**How trust changes:**

```
1. Initial registration:
   - peer add → trust = 0.0
   - peer add --self → trust = 1.0

2. Outcome closure on imported blocks:
   - When outcome(block_ids, signal) runs on blocks with source_peer set:
     - signal >= 0.7 → trust += delta (positive outcome)
     - signal <= 0.3 → trust -= delta (negative outcome)
     - delta = 0.05 × weight (small, gradual)
   - Trust is clamped to [0.0, 1.0]

3. Trust decay:
   - During curate(), if peer has had no interaction for > 90 days:
     - trust *= 0.95 (slow decay)
   - This prevents stale trust from persisting indefinitely

4. Manual override:
   - elfmem peer trust <did> --set <value>
   - For cases where the agent or user wants to adjust directly
```

**Auto-ingest threshold:**

```
trust >= 0.7 → imported blocks enter inbox at confidence_floor × 1.5 (0.45)
trust < 0.7  → imported blocks enter inbox at confidence_floor (0.3)
trust = 1.0  → self-merge: preserve original confidence
```

v1 does not auto-reject low-trust imports. All imports go through `dream()`, which handles dedup and contradiction. Trust affects starting confidence, not admission.

**Why global trust is sufficient for v1:** Topic-scoped trust (trust this peer on gilts but not on crypto) is elegantly modelled by mind blocks. `mind:trader-elf` already tracks beliefs, accuracy, blindspots per topic. The global trust score gates import behavior; the mind block models the nuance. Two mechanisms, complementary, not redundant.

---

## 6. Result Types

All new operations return typed result objects following the agent-first contract.

```python
@dataclass
class ExportResult:
    """Result of elfmem export."""
    blocks_exported: int
    edges_exported: int
    output_path: str
    from_did: str
    share_level: str

    @property
    def summary(self) -> str:
        return f"Exported {self.blocks_exported} blocks, {self.edges_exported} edges to {self.output_path}"

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocks_exported": self.blocks_exported,
            "edges_exported": self.edges_exported,
            "output_path": self.output_path,
            "from_did": self.from_did,
            "share_level": self.share_level,
        }


@dataclass
class ImportResult:
    """Result of elfmem import."""
    blocks_imported: int
    blocks_skipped: int          # already existed (dedup)
    edges_imported: int
    from_peer: str
    is_self_merge: bool
    confidence_floor: float

    @property
    def summary(self) -> str:
        mode = "self-merge" if self.is_self_merge else f"peer ({self.from_peer})"
        return (
            f"Imported {self.blocks_imported} blocks ({self.blocks_skipped} skipped) "
            f"from {mode}, {self.edges_imported} edges"
        )

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocks_imported": self.blocks_imported,
            "blocks_skipped": self.blocks_skipped,
            "edges_imported": self.edges_imported,
            "from_peer": self.from_peer,
            "is_self_merge": self.is_self_merge,
            "confidence_floor": self.confidence_floor,
        }


@dataclass
class PeerSendResult:
    """Result of sending a message to a peer."""
    block_id: str
    msg_id: str
    to_peer: str
    outbox_path: str
    in_reply_to: str | None = None

    @property
    def summary(self) -> str:
        reply = f" (reply to {self.in_reply_to})" if self.in_reply_to else ""
        return f"Sent {self.msg_id} to {self.to_peer}{reply}"

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "msg_id": self.msg_id,
            "to_peer": self.to_peer,
            "outbox_path": self.outbox_path,
            "in_reply_to": self.in_reply_to,
        }


@dataclass
class PeerInboxResult:
    """Result of checking the peer inbox."""
    messages_found: int
    messages_imported: int
    messages_skipped: int        # already imported (dedup)
    peers: list[str]             # DIDs of peers with pending messages

    @property
    def summary(self) -> str:
        if self.messages_found == 0:
            return "No pending messages."
        return (
            f"Found {self.messages_found} messages from {len(self.peers)} peers. "
            f"Imported {self.messages_imported}, skipped {self.messages_skipped}."
        )

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages_found": self.messages_found,
            "messages_imported": self.messages_imported,
            "messages_skipped": self.messages_skipped,
            "peers": self.peers,
        }


@dataclass
class PeerInfo:
    """Summary of a registered peer."""
    did: str
    name: str
    trust: float
    is_self: bool
    first_contact: str
    last_active: str
    blocks_imported: int
    blocks_exported: int
    messages_in: int
    messages_out: int

    @property
    def summary(self) -> str:
        kind = "self" if self.is_self else f"trust={self.trust:.2f}"
        return f"{self.name} ({self.did}) [{kind}] — {self.messages_in}↓ {self.messages_out}↑"

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "did": self.did,
            "name": self.name,
            "trust": self.trust,
            "is_self": self.is_self,
            "first_contact": self.first_contact,
            "last_active": self.last_active,
            "blocks_imported": self.blocks_imported,
            "blocks_exported": self.blocks_exported,
            "messages_in": self.messages_in,
            "messages_out": self.messages_out,
        }
```

---

## 7. CLI Commands

All commands under the `elfmem peer` subcommand group.

```
elfmem peer init --name <name>              # Set this instance's identity
elfmem peer add <did> --name <name> [--self] # Register a peer
elfmem peer remove <did>                     # Unregister a peer
elfmem peer list [--json]                    # Show all peers with trust scores
elfmem peer trust <did> [--set <float>]      # View/set trust
elfmem peer send <did> <content> [--reply-to <msg_id>] [--json]
elfmem peer inbox [--from <did>] [--import-all] [--json]
elfmem export [--share public|peer|all] [--to <did>] [--self] [--min-confidence <float>] -o <path> [--json]
elfmem import <path> [--from <did>] [--self-merge] [--json]
```

**Note:** `export` and `import` are top-level commands (not under `peer`) because they operate on blocks generally. The `peer` subgroup handles identity and messaging.

### 7.1 MCP tools

Corresponding MCP tools for agent use:

```
elfmem_peer_init(name)
elfmem_peer_add(did, name, is_self)
elfmem_peer_list()
elfmem_peer_trust(did, set_value?)
elfmem_peer_send(to_did, content, in_reply_to?)
elfmem_peer_inbox(from_did?, import_all?)
elfmem_export(share_level, to_peer?, output_path, min_confidence?)
elfmem_import(path, from_peer?, self_merge?)
```

---

## 8. API Surface

New methods on `MemorySystem`:

```python
# Identity
async def peer_init(self, name: str) -> str:
    """Set this instance's identity. Returns the DID."""

# Peer management
async def peer_add(self, did: str, name: str, *, is_self: bool = False) -> PeerInfo:
    """Register a peer. is_self=True sets trust=1.0."""

async def peer_remove(self, did: str) -> bool:
    """Unregister a peer. Returns True if existed."""

async def peer_list(self) -> list[PeerInfo]:
    """List all registered peers."""

async def peer_trust(self, did: str, *, set_value: float | None = None) -> PeerInfo:
    """Get or set trust for a peer."""

# Messaging
async def peer_send(
    self, to_peer: str, content: str, *, in_reply_to: str | None = None,
) -> PeerSendResult:
    """Send a message to a peer. Heartbeat speed."""

async def peer_inbox(
    self, *, from_peer: str | None = None, import_all: bool = False,
) -> PeerInboxResult:
    """Check and optionally import pending messages."""

# Knowledge exchange
async def export_blocks(
    self, *, share_level: str = "public", to_peer: str | None = None,
    min_confidence: float = 0.0, output_path: str,
) -> ExportResult:
    """Export shareable blocks as a JSON bundle."""

async def import_blocks(
    self, path: str, *, from_peer: str | None = None,
    self_merge: bool = False,
) -> ImportResult:
    """Import a block bundle with provenance tracking."""
```

---

## 9. Consolidation Changes

### 9.1 Message blocks skip dedup

Messages are events ("I said X at time T"), not knowledge claims ("X is true"). Deduplicating messages is semantically wrong.

In `consolidate.py`, add a guard in the dedup check:

```python
# In the per-block processing loop:
if block["category"] == "message":
    # Messages always promote — they are events, not claims
    decisions.append(_BlockDecision(
        block_id=block_id,
        action="promote",
        supersedes_id=None,
        inferred_tags=existing_tags,
        confidence=block["confidence"],
        # ... other fields
    ))
    continue  # Skip dedup and contradiction checks
```

Messages still get:
- Embeddings (searchable: "what did trader-elf say about gilts?")
- Edges to related knowledge blocks (connected to the knowledge graph)
- Self-alignment scoring (how aligned is this message with my identity)

Messages do NOT get:
- Near-duplicate rejection (two similar messages from different peers are both valid)
- Contradiction detection (a message is a stance, not a fact claim to verify)

### 9.2 Peer-originated blocks preserve provenance

During consolidation, `source_peer` is carried through from inbox to active. The tag `peer/<did>` is added during import and preserved through consolidation. No changes needed to the promotion logic — tags are already preserved.

### 9.3 Trust update during outcome

In `outcome.py`, when outcome closure runs on blocks with `source_peer`:

```python
# After updating block confidence:
if block["source_peer"]:
    peer = await get_peer(conn, block["source_peer"])
    if peer:
        trust_delta = 0.05 * weight
        if signal >= 0.7:
            new_trust = min(1.0, peer["trust"] + trust_delta)
        elif signal <= 0.3:
            new_trust = max(0.0, peer["trust"] - trust_delta)
        else:
            new_trust = peer["trust"]  # Neutral signal, no change
        await update_peer_trust(conn, block["source_peer"], new_trust)
```

This closes the trust loop: peer sends knowledge → I use it → outcome says it helped → trust rises. Entirely driven by existing machinery. The only new code is the trust update, piggybacking on outcome.

---

## 10. Configuration

New section in `ElfmemConfig`:

```python
class PeerConfig(BaseModel):
    """Configuration for peer communication."""

    identity: str | None = None                 # This instance's DID (set by peer init)
    outbox_dir: str = "~/.elfmem/outbox"        # Where outbound messages are written
    inbox_dir: str = "~/.elfmem/inbox"          # Where inbound messages are read from
    confidence_floor: float = 0.3               # Starting confidence for imported peer blocks
    auto_ingest_trust_threshold: float = 0.7    # Trust level for elevated confidence import
    trust_decay_days: int = 90                  # Days of inactivity before trust decays
    trust_decay_factor: float = 0.95            # Multiplicative decay per curate cycle
```

All fields have sensible defaults. Zero config required to start.

---

## 11. Complete Message Exchange Walkthrough

### Scenario: Alv asks Trader-elf about gilts

**On Alv's side (the asker):**

```bash
# 1. Agent (Claude Code) decides to ask trader-elf
#    Uses frames to compose the question:
#    - recall --frame attention "gilts BoE"        → relevant knowledge
#    - recall --frame simulate "trader perspective" → model trader's viewpoint

# 2. Agent composes question and sends:
elfmem peer send elf:trader-elf \
  "Looking at gilts, I think BoE holds longer than curve prices. What's your read?"

# Result:
# Sent m_a1b2c3d4 to elf:trader-elf
# → Block created in inbox (category=message, direction=outbound)
# → File written to ~/.elfmem/outbox/elf-trader-elf/msg_m_a1b2c3d4.json

# 3. Transport moves the file (rsync, shared folder, manual copy)
```

**On Trader-elf's side (the responder):**

```bash
# 4. Agent checks inbox (during session start or periodic check):
elfmem peer inbox --import-all

# Result:
# Found 1 message from 1 peer. Imported 1, skipped 0.
# → Block created in inbox (category=message, direction=inbound, source_peer=elf:laptop-elf)

# 5. Agent reads the message, uses frames to compose reply:
#    - recall --frame attention "gilts BoE SONIA"  → own research
#    - recall --frame self "communication"          → voice and boundaries

# 6. Agent composes reply:
elfmem peer send elf:laptop-elf \
  "Right on BoE but wrong on curve — front end repriced. Watch SONIA Thursday." \
  --reply-to m_a1b2c3d4

# Result:
# Sent m_e5f6g7h8 to elf:laptop-elf (reply to m_a1b2c3d4)
# → Block created + outbox file written
# → "replies_to" edge: m_e5f6g7h8 → m_a1b2c3d4
```

**Back on Alv's side:**

```bash
# 7. Agent checks inbox:
elfmem peer inbox --import-all

# Result:
# Found 1 message from 1 peer. Imported 1, skipped 0.
# → Reply block ingested with in_reply_to chain

# 8. Later, SONIA fixings confirm trader-elf's view:
elfmem outcome <reply_block_id> 0.9 --source "SONIA fixings confirmed gilt view"

# → Confidence on reply block rises
# → Trust on elf:trader-elf rises by 0.05 × 1.0 = 0.05
# → mind:trader-elf can be updated: "accurate on fixed income flow"

# 9. dream() runs:
# → Both messages get embedded, edges created to gilt-related knowledge
# → Conversation is now searchable and connected to the knowledge graph
```

**The complete loop:**
Ask → Transport → Receive → Compose → Reply → Transport → Receive → Outcome → Trust update

Every step uses existing elfmem primitives. No new pipeline. No new process.

---

## 12. Self-Federation Walkthrough

### Scenario: Laptop-elf syncs with Server-elf

```bash
# On server (cron job, weekly):
elfmem export --all --self -o /shared/sync/server-export-2026-04-29.json
# → Exports all active blocks with full confidence, all edges

# On laptop:
elfmem import /shared/sync/server-export-2026-04-29.json --self-merge
# → Blocks imported at original confidence (trust=1.0)
# → Dedup handles blocks that exist on both sides
# → New blocks enter inbox, get processed by dream()
# → Edges between shared blocks are merged

# On laptop (export the other way):
elfmem export --all --self -o /shared/sync/laptop-export-2026-04-29.json

# On server:
elfmem import /shared/sync/laptop-export-2026-04-29.json --self-merge
```

Bidirectional sync via two export/import cycles. No conflict resolution protocol needed — `dream()` handles dedup, and `--self-merge` preserves confidence.

---

## 13. Edge Cases and Mitigations

| Edge case | Risk | Mitigation |
|-----------|------|------------|
| **Import same bundle twice** | Duplicate blocks | Content-hash dedup in learn() rejects exact duplicates in inbox; dream() rejects near-duplicates in active |
| **Peer sends contradictory knowledge** | Bad knowledge enters memory | dream() runs contradiction detection; both blocks survive; agent resolves through reasoning |
| **Self-merge conflict (same block modified on both sides)** | Which version wins? | Later write wins (by created_at timestamp). Both versions go through dream(); near-dup detection archives the older one |
| **Message with prompt injection** | Adversarial instructions in peer message | Messages stored as data blocks with `category=message` and `source_peer` set. When retrieved via frames, they're clearly peer-originated. The agent process (not elfmem) decides how to use the content |
| **Outbox fills up (transport not running)** | Disk space | Outbox files are small JSON. Warn in `elfmem status` if outbox has > 100 unsent files |
| **Inbox import of block that references unknown edges** | Dangling edge targets | Edge import skips edges where either endpoint wasn't imported. Safe — edges can reform via dream() cosine similarity |
| **Unknown peer in inbox (not registered)** | Messages from unregistered peers | `peer inbox` warns about messages from unknown peers; does not import unless `--from` explicitly names them or they're registered |
| **Trust overflow (many positive outcomes)** | Trust goes above 1.0 | Clamped to [0.0, 1.0] |
| **Self-peer sends malformed bundle** | Import crashes | Validate bundle JSON schema before processing; return ImportResult with errors list |
| **Circular reply chains** | Infinite conversation depth | No risk — elfmem is passive. The agent decides whether to reply. Reply chains are just edge graphs; no automatic response generation |
| **Large bundle (thousands of blocks)** | Slow import | Import is I/O-bound (SQLite inserts). Batching in chunks of 100. Progress logged. |
| **Category=message blocks in export** | Exporting private conversations | Messages have `share=private` by default. Only explicitly shared messages appear in exports. |

---

## 14. What v1 Does NOT Include

Explicitly deferred to v2 or later:

| Feature | Why deferred | v2 prerequisite |
|---------|-------------|-----------------|
| **Ed25519 signing** | No automated transport yet; manual import provides contextual trust | Automated transport (HTTP adapter) |
| **did:web / did:key** | Simple `elf:<name>` is sufficient for manual registration | Public-facing agents |
| **Agent cards** | No discovery mechanism yet | Registry or mDNS |
| **mDNS / LAN discovery** | Manual peer add is sufficient | Multiple agents on same network |
| **Capability tokens** | Trust + rate limiting is sufficient | Inter-organisation communication |
| **Public boards** | No demonstrated demand | Community of elfmem users |
| **Redaction tiers** | `share` field (private/public/peer) is sufficient | Competitive/sensitive domains |
| **Topic-scoped trust** | Mind blocks model per-topic accuracy | More than 5 active peers |
| **Mesh topology viz** | No network to visualize yet | 3+ communicating instances |
| **Lesson transfer protocol** | Export with `--min-confidence` covers this | Formalised lesson schema |
| **Introduction-by-peer** | Manual registration is sufficient | Open network with unknown peers |
| **Transport adapters (HTTP, WebSocket)** | File-based exchange works | Always-on agents |
| **Audit log extensions** | Standard elfmem logging is sufficient | Compliance requirements |

---

## 15. Testing Strategy

### 15.1 Unit tests (new file: `tests/test_peer.py`)

All tests use in-memory SQLite + mock services. No file I/O for database tests.

**Schema tests:**
- `test_peer_roster_table_exists` — table created on init
- `test_blocks_source_peer_column` — column exists, nullable, defaults to None
- `test_blocks_share_column` — column exists, defaults to "private"
- `test_blocks_envelope_json_column` — column exists, nullable

**Peer management tests:**
- `test_peer_init_sets_identity` — identity stored in config
- `test_peer_add_creates_roster_entry` — new peer at trust 0.0
- `test_peer_add_self_sets_trust_one` — `--self` sets trust=1.0 and is_self=True
- `test_peer_add_duplicate_rejected` — idempotent: same DID doesn't create duplicate
- `test_peer_remove_deletes_entry` — peer removed from roster
- `test_peer_list_returns_all` — all registered peers returned
- `test_peer_trust_get` — returns current trust
- `test_peer_trust_set` — updates trust, clamped to [0.0, 1.0]

**Export tests:**
- `test_export_public_blocks_only` — private blocks excluded
- `test_export_excludes_self_tags` — `self/*` blocks never exported
- `test_export_includes_edges` — edges between exported blocks included
- `test_export_excludes_inbox_and_archived` — only active blocks
- `test_export_min_confidence_filter` — blocks below threshold excluded
- `test_export_peer_level_filtering` — peer-level share filter works
- `test_export_bundle_format` — JSON schema matches spec

**Import tests:**
- `test_import_creates_blocks_in_inbox` — imported blocks have status=inbox
- `test_import_sets_source_peer` — source_peer field set correctly
- `test_import_adds_provenance_tag` — `peer/<did>` tag added
- `test_import_dedup_exact` — duplicate content-hash blocks skipped
- `test_import_self_merge_preserves_confidence` — is_self_merge=True keeps original confidence
- `test_import_peer_uses_confidence_floor` — non-self blocks start at confidence_floor
- `test_import_edges_between_imported_blocks` — edges imported where both endpoints exist
- `test_import_skips_edges_with_missing_endpoints` — graceful skip
- `test_import_updates_roster_stats` — blocks_imported counter incremented
- `test_import_unknown_peer_raises` — error with recovery suggestion

**Message tests:**
- `test_peer_send_creates_message_block` — category=message, correct tags
- `test_peer_send_writes_outbox_file` — JSON file in outbox directory
- `test_peer_send_envelope_json` — envelope metadata correct
- `test_peer_send_reply_creates_edge` — replies_to edge created
- `test_peer_inbox_finds_messages` — scans inbox directory
- `test_peer_inbox_imports_messages` — creates blocks with source_peer
- `test_peer_inbox_dedup` — already-imported messages skipped
- `test_peer_inbox_unknown_peer_warns` — unregistered peer flagged
- `test_message_blocks_skip_dedup_in_consolidate` — messages always promoted
- `test_message_blocks_get_embeddings` — messages are searchable after dream()

**Trust tests:**
- `test_outcome_updates_peer_trust_positive` — signal >= 0.7 increases trust
- `test_outcome_updates_peer_trust_negative` — signal <= 0.3 decreases trust
- `test_outcome_neutral_no_trust_change` — signal 0.4-0.6 no change
- `test_trust_clamped_to_bounds` — never below 0.0 or above 1.0
- `test_trust_decay_on_inactivity` — curate() decays inactive peer trust

### 15.2 Integration tests

- `test_full_export_import_cycle` — export from instance A, import to instance B, verify provenance
- `test_full_message_exchange` — send, transport (file copy), inbox, import, reply
- `test_self_federation_roundtrip` — export --self, import --self-merge, verify confidence preserved
- `test_import_then_dream_promotes` — imported blocks go through consolidation
- `test_outcome_on_imported_block_updates_trust` — full trust loop closure

### 15.3 File I/O tests (use `tmp_path`)

Outbox/inbox tests that write actual files use pytest's `tmp_path` fixture:

```python
@pytest.fixture
def outbox_dir(tmp_path):
    d = tmp_path / "outbox"
    d.mkdir()
    return d

@pytest.fixture
def inbox_dir(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    return d
```

---

## 16. File Changes

### New files:

| File | Purpose | Est. lines |
|------|---------|-----------|
| `src/elfmem/operations/peer.py` | Export, import, send, inbox operations | ~300 |
| `src/elfmem/peer.py` | Peer roster management (add, remove, list, trust) | ~120 |
| `src/elfmem/peer_types.py` | ExportBundle, envelope schema, PeerSendResult, etc. | ~150 |
| `tests/test_peer.py` | All peer-related tests | ~500 |

### Modified files:

| File | Change | Est. delta |
|------|--------|-----------|
| `src/elfmem/db/models.py` | Add `source_peer`, `share`, `envelope_json` columns; `peer_roster` table | +30 |
| `src/elfmem/db/queries.py` | Peer roster CRUD queries; block queries with source_peer filter | +80 |
| `src/elfmem/types.py` | New result types (ExportResult, ImportResult, PeerSendResult, PeerInboxResult, PeerInfo) | +120 |
| `src/elfmem/api.py` | New public methods: peer_init, peer_add, peer_send, peer_inbox, export_blocks, import_blocks | +150 |
| `src/elfmem/cli.py` | `peer` subcommand group + `export`/`import` commands | +120 |
| `src/elfmem/config.py` | PeerConfig model | +15 |
| `src/elfmem/operations/consolidate.py` | Message block dedup guard | +8 |
| `src/elfmem/operations/outcome.py` | Trust update on peer-originated blocks | +15 |
| `src/elfmem/operations/curate.py` | Trust decay for inactive peers | +12 |
| `src/elfmem/__init__.py` | Export new types | +8 |
| `src/elfmem/mcp_server.py` | New MCP tools for peer operations | +80 |
| `CHANGELOG.md` | Document new features | +15 |

**Total estimated new code: ~800 lines production, ~500 lines tests.**

---

## 17. Implementation Sequence

### Step 1: Schema + Peer Roster (foundation)

- Add columns to `blocks` table in models.py
- Create `peer_roster` table in models.py
- Write migration function (idempotent ALTER TABLE)
- Add PeerConfig to config.py
- Write peer roster CRUD queries in queries.py
- Write peer management functions (peer.py)
- Add peer_init, peer_add, peer_remove, peer_list, peer_trust to api.py
- Tests: schema tests, peer management tests

**Validates:** Schema works, migration is idempotent, roster CRUD is correct.

### Step 2: Export + Import (knowledge exchange)

- Write export_blocks function (operations/peer.py)
- Write import_blocks function (operations/peer.py)
- Define ExportBundle JSON schema
- Add share field enforcement (self/* blocks always private)
- Add consolidation guard for message blocks
- Add trust-gated confidence floor logic
- Add export_blocks, import_blocks to api.py
- Tests: export tests, import tests, integration tests

**Validates:** Knowledge flows between instances with correct provenance and confidence.

### Step 3: Messaging (peer send + inbox)

- Write peer_send function (operations/peer.py) — learn() + outbox write
- Write peer_check_inbox function (operations/peer.py) — scan + import
- Add envelope_json handling
- Add reply chain edge creation
- Add peer_send, peer_inbox to api.py
- Tests: message tests, reply chain tests, file I/O tests

**Validates:** Messages flow between instances, reply chains form conversation graphs.

### Step 4: Trust Loop (outcome → trust)

- Modify outcome.py to update peer trust on source_peer blocks
- Modify curate.py to decay inactive peer trust
- Tests: trust update tests, trust decay tests, full loop integration test

**Validates:** Trust evolves based on real outcomes. The feedback loop closes.

### Step 5: CLI + MCP (user surface)

- Add `peer` subcommand group to cli.py
- Add `export` and `import` commands to cli.py
- Add MCP tools to mcp_server.py
- Update CHANGELOG.md

**Validates:** Full user-facing surface works end-to-end.

---

## 18. Kill Criteria

The design is wrong if:

1. **Export/import adds more than ~800 lines of production code** → too complex for the value
2. **Schema changes break any existing test** → too invasive (migration is wrong)
3. **Self-federation via export/import requires manual conflict resolution > 10% of the time** → need dedicated sync
4. **Trust scoring requires constant manual override** → wrong model
5. **Message blocks pollute knowledge retrieval** → category filtering or frame adjustments needed
6. **Nobody uses it within 30 days** → premature

---

## 19. Success Criteria (4 weeks)

1. Two elfmem instances exchange blocks via export/import. Imported blocks have correct provenance, go through dream(), and are searchable.
2. A message sent from instance A reaches instance B via outbox/inbox file transfer. Reply chain creates a connected conversation graph.
3. Self-federation works: laptop and server sync bidirectionally via export --self / import --self-merge.
4. At least one outcome closure on an imported block updates the source peer's trust score.
5. All existing tests pass with zero changes (schema migration is additive-only).
6. Total new code is under 1,300 lines (800 production + 500 tests).

---

## 20. What This Unlocks

### For learning
- Agents learn from each other's outcomes. Knowledge that survived outcome closure on one instance can be imported by another, starting with appropriate humility (low confidence) and earning its way up through local validation.

### For consciousness
- An agent that knows other agents exist — that models them (mind blocks), communicates with them (messages), and learns from them (import + outcome) — has a richer inner world. The self/other boundary becomes a real cognitive distinction, not a metaphor.
- The trust loop (send → receive → outcome → trust update) is a social feedback loop. It's how agents develop preferences about other agents based on evidence, not instructions.

### For the elfmem product
- A reason to run multiple instances (specialisation pays off when knowledge transfers)
- A reason to trust elfmem as infrastructure (provenance tracking, trust scoring)
- A foundation for agent marketplaces (agentmkts becomes messages + trust + outcome)

### What v2 adds (when v1 is validated)
- Ed25519 signing (provenance verification without manual trust)
- HTTP transport adapter (automated message delivery)
- Topic-scoped trust (per-domain accuracy tracking)
- Introduction-by-peer (trust bootstrapping via vouching)
- Agent cards + did:web (public identity)
- Public boards (broadcast knowledge sharing)

---

## Appendix A: ExportBundle JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["version", "exported_at", "from_did", "block_count", "blocks"],
  "properties": {
    "version": { "type": "integer", "const": 1 },
    "exported_at": { "type": "string", "format": "date-time" },
    "from_did": { "type": "string" },
    "block_count": { "type": "integer" },
    "blocks": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "content", "category", "tags", "confidence", "created_at"],
        "properties": {
          "id": { "type": "string" },
          "content": { "type": "string" },
          "category": { "type": "string" },
          "tags": { "type": "array", "items": { "type": "string" } },
          "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
          "created_at": { "type": "string", "format": "date-time" },
          "share": { "type": "string", "enum": ["public", "peer"] },
          "envelope_json": { "type": ["string", "null"] }
        }
      }
    },
    "edges": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["from_id", "to_id", "relation_type", "weight"],
        "properties": {
          "from_id": { "type": "string" },
          "to_id": { "type": "string" },
          "relation_type": { "type": "string" },
          "weight": { "type": "number", "minimum": 0, "maximum": 1 }
        }
      }
    }
  }
}
```

## Appendix B: Outbox/Inbox Message File Format

```json
{
  "version": 1,
  "msg_id": "m_a1b2c3d4",
  "from_did": "elf:laptop-elf",
  "to_did": "elf:trader-elf",
  "in_reply_to": null,
  "sent_at": "2026-04-29T14:30:00Z",
  "content": "What is your view on UK gilts this week?",
  "tags": ["peer/outbound", "peer/to:elf:trader-elf"],
  "category": "message"
}
```

## Appendix C: Directory Layout

```
~/.elfmem/
  config.yaml                    # includes peer.identity
  databases/
    elfmem.db                    # existing database (now with peer columns)
  outbox/                        # outbound messages (per-peer subdirs)
    elf-trader-elf/
      msg_m_a1b2c3d4.json
  inbox/                         # inbound messages (per-peer subdirs)
    elf-trader-elf/
      msg_m_x1y2z3w4.json
    processed/                   # already-imported messages (audit trail)
      msg_m_x1y2z3w4.json
```
