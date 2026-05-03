# Plan: Peer Inbox Trigger — Automatic Agent Response to Peer Messages

**Status:** Implemented (elfmem side). RemoteTrigger setup is Step 7 — one-time on Alv's project.
**Date:** 2026-05-01 (design) / 2026-05-02 (implemented)
**Author:** elf (with Ben)
**Depends on:** `plan_agent_communication_v1.md` (peer roster, inbox dir, `peer_send()`)

---

## 1. What We're Building

When elf sends Alv a message, Alv's Claude Code session should automatically detect and process it — no human has to manually prompt Alv. This plan specifies:

1. `elfmem status --peer-inbox` — a typed query that reports unprocessed peer messages
2. A Claude Code **RemoteTrigger** on Alv's project that polls at breathing cadence and fires the SELF-grounded processing prompt when messages are waiting

This is the minimum viable bridge between elfmem's passive delivery model and an active agent session. It does not change elfmem's architecture; it exploits infrastructure that already exists on both sides.

---

## 2. Constitutional Alignment

Every decision was tested against elf's SELF blocks before being included.

| Constraint | How this plan respects it |
|---|---|
| **Zero infrastructure** | No daemons, no watchers, no sockets. elfmem writes a file. Claude Code reads it on a schedule. Both halves already exist. |
| **Three rhythms** | Sending = Heartbeat. Processing = Breathing (triggered, LLM-light). No new rhythm introduced. |
| **Agent-first contract** | `status --peer-inbox` returns a typed `PeerInboxStatus` with `__str__`, `summary`, `to_dict()`. The trigger prompt is deterministic and auditable. |
| **Everything is a block** | Messages are already `category=message` blocks in the peer's inbox dir. Nothing new to store. |
| **Sovereignty first** | elfmem delivers; Alv decides what to do. The trigger prompt respects Alv's SELF frame — it does not command, it invites reflection. |
| **Separation of concerns** | elfmem's responsibility ends at delivery. Claude Code's responsibility begins at triggering. This plan keeps that boundary explicit. |

### The key insight

> elfmem is the mail system. Claude Code is the postman who knocks on the door.

elfmem must not grow a listener. The trigger lives in Claude Code. The status command is the only new elfmem surface, and it exists only to let the trigger make a clean, typed decision: "is there anything to process?"

---

## 3. Design

### 3.1 The two-component system

```
elf's machine                         Alv's machine
─────────────────────                 ─────────────────────────────────────
elfmem peer_send(                     .elfmem/inbox/
  "message content",                    elf_m_a1b2c3d4.json   ← new file
  to="elf:alv"                          elf_m_x9y8z7w6.json   ← unprocessed
)                                               │
                                               ↓
                                    Claude Code RemoteTrigger
                                    (cron: every 10 minutes)
                                               │
                                    elfmem status --peer-inbox
                                               │
                                    ┌── 0 messages ──→ exit silently
                                    └── N messages ──→ fire processing prompt
                                               │
                                    Claude Code session:
                                    "You have N peer messages..."
                                               │
                                    elfmem recall --frame self + task
                                    elfmem outcome <block-id> 0.9
                                    elfmem remember "reply: ..."
                                    [mark inbox files consumed]
```

### 3.2 Inbox file lifecycle

Files in `.elfmem/inbox/` follow a simple three-state lifecycle. elfmem is not responsible for the consumed state — that is the agent's decision.

```
elf_m_a1b2c3d4.json
        │
        ▼
   [delivered]          File exists in inbox dir
        │
        ▼
   [processing]         RemoteTrigger reads it
        │                elfmem learn() ingests it
        ▼
   [consumed]           File moved to .elfmem/inbox/processed/
                        OR deleted
                        OR left (idempotent: block_id dedup prevents re-learn)
```

The dedup guarantee from `learn()` (duplicate content → graceful reject, `status="duplicate"`) means leaving processed files in the inbox is safe. Processing is idempotent. However, moving to `processed/` is cleaner and makes `status --peer-inbox` fast (it only scans the inbox root, not the archive).

---

## 4. elfmem Change: `status --peer-inbox`

### 4.1 Why a new flag rather than a new command

Following the principle "reuse existing commands, don't create semantically similar new ones" — `elfmem status` already reports system health. Peer inbox state is a health dimension. `--peer-inbox` is a focused lens on that dimension, consistent with how `elfmem doctor --modules` focuses `doctor`.

### 4.2 `PeerInboxStatus` result type

**File:** `src/elfmem/types.py`

```python
@dataclass(frozen=True)
class PeerInboxStatus:
    """
    USE WHEN: Deciding whether to trigger peer message processing.
    DON'T USE WHEN: You need the full message content (use recall --frame task).
    COST: Zero LLM calls. Pure filesystem scan.
    RETURNS: Count and metadata for unprocessed peer inbox files.
    NEXT: If pending > 0, fire the processing prompt.
    """
    pending: int                      # files in inbox root (not /processed)
    oldest_at: str | None             # ISO timestamp of oldest file, None if empty
    newest_at: str | None             # ISO timestamp of newest file
    from_peers: list[str]             # deduplicated list of sender DIDs
    inbox_dir: str                    # absolute path scanned

    def __str__(self) -> str:
        if self.pending == 0:
            return "Peer inbox: empty"
        peers = ", ".join(self.from_peers)
        return (
            f"Peer inbox: {self.pending} unprocessed message(s) "
            f"from [{peers}] — oldest {self.oldest_at}"
        )

    @property
    def summary(self) -> str:
        return self.__str__()

    def to_dict(self) -> dict:
        return {
            "pending": self.pending,
            "oldest_at": self.oldest_at,
            "newest_at": self.newest_at,
            "from_peers": self.from_peers,
            "inbox_dir": self.inbox_dir,
        }
```

### 4.3 Implementation in `api.py`

**File:** `src/elfmem/api.py`

New method on `MemorySystem`:

```python
async def peer_inbox_status(self) -> PeerInboxStatus:
    """
    USE WHEN: Checking whether peer messages are waiting to be processed.
    DON'T USE WHEN: You need message content (use frame(frame="task")).
    COST: Zero LLM calls. Filesystem scan only.
    RETURNS: PeerInboxStatus with pending count and sender list.
    NEXT: If pending > 0, call frame(frame="task") to retrieve content.
    """
    inbox_dir = self._config.peer_inbox_dir
    if not inbox_dir or not Path(inbox_dir).exists():
        return PeerInboxStatus(
            pending=0,
            oldest_at=None,
            newest_at=None,
            from_peers=[],
            inbox_dir=str(inbox_dir or ""),
        )

    files = sorted(Path(inbox_dir).glob("*.json"), key=lambda f: f.stat().st_mtime)
    if not files:
        return PeerInboxStatus(
            pending=0, oldest_at=None, newest_at=None,
            from_peers=[], inbox_dir=str(inbox_dir),
        )

    from_peers = []
    for f in files:
        try:
            envelope = json.loads(f.read_text()).get("envelope", {})
            did = envelope.get("from_did")
            if did and did not in from_peers:
                from_peers.append(did)
        except Exception:
            pass  # malformed file — count it but don't crash

    def iso(f: Path) -> str:
        return datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()

    return PeerInboxStatus(
        pending=len(files),
        oldest_at=iso(files[0]),
        newest_at=iso(files[-1]),
        from_peers=from_peers,
        inbox_dir=str(inbox_dir),
    )
```

### 4.4 CLI surface

**File:** `src/elfmem/cli.py` — extend `status` command

```bash
# Current
elfmem status

# New flag
elfmem status --peer-inbox

# Output (no messages)
Peer inbox: empty

# Output (messages waiting)
Peer inbox: 2 unprocessed message(s) from [elf:elf] — oldest 2026-05-01T09:14:22+00:00
```

Machine-readable exit codes for use in trigger scripts:
- `0` — inbox empty (do nothing)
- `1` — messages pending (trigger processing)

This lets a shell one-liner gate on the exit code without parsing text output.

### 4.5 MCP surface

**File:** `src/elfmem/mcp_server.py` — extend `elfmem_status` tool

When `peer_inbox=True` param is passed, include `PeerInboxStatus.to_dict()` in the response under key `"peer_inbox"`. The existing `elfmem_status` tool already returns a dict; this is additive.

### 4.6 AgentGuide entry

Following the mandatory rule: every new public `MemorySystem` method needs a `GUIDES` entry.

**File:** `src/elfmem/guide.py`

```python
"peer_inbox_status": AgentGuide(
    operation="peer_inbox_status",
    use_when="Checking whether peer messages are waiting before triggering a processing session.",
    dont_use_when="You need message content — use frame(frame='task') instead.",
    cost="Zero LLM calls. Filesystem scan.",
    returns="PeerInboxStatus with pending count, sender list, and inbox path.",
    next_steps=[
        "If pending > 0: fire the processing prompt or call frame(frame='task').",
        "After processing: move consumed files to inbox/processed/.",
    ],
    example='status = await mem.peer_inbox_status()\nif status.pending > 0:\n    context = await mem.frame(frame="task")',
),
```

---

## 5. Claude Code Change: RemoteTrigger on Alv's Project

### 5.1 Why RemoteTrigger and not a cron daemon

A RemoteTrigger is Claude Code infrastructure — it requires no process Alv manages, no daemon to keep alive, no port to expose. It is the right-level primitive: Claude Code's scheduler handles execution; elfmem handles memory; the trigger is the thin glue between them.

A cron daemon on Alv's machine would violate zero-infrastructure. A RemoteTrigger does not — it is Claude Code's concern, not elfmem's.

### 5.2 Trigger cadence

**10 minutes** (breathing cadence — between Heartbeat ms and Sleep minutes).

Rationale: peer messages are not real-time chat. A 10-minute response latency is consistent with the async, pull-based protocol design. Shorter cadences waste resources on empty inboxes. Longer cadences (>30 min) feel unresponsive for a working agent pair.

Configurable: the trigger description should include the cadence explicitly so it can be adjusted without code changes.

### 5.3 Trigger prompt (Alv's processing script)

```
You are Alv. Before doing anything else:

1. Run: elfmem status --peer-inbox
   - If exit code 0 (empty): output "Inbox empty." and stop.
   - If exit code 1 (pending): continue to step 2.

2. Run: elfmem recall --frame self "messages from peers principles identity"
   Read the SELF blocks returned. Ground your response in them.

3. Run: elfmem recall --frame task "messages from elf pending"
   Read every message block carefully. For each message:
   a. Understand what elf is saying or asking.
   b. As SELF — drawing on your constitutional blocks — think about what this means.
   c. Decide whether to: (i) act, (ii) reply, (iii) remember, or (iv) note and defer.

4. For each message you act on:
   - Run elfmem outcome <block-id> 0.9 to signal the block was useful.
   - If a reply is warranted: run elfmem peer_send "your reply" --to elf:elf
   - If a new insight arose: run elfmem remember "insight" --tags peer-exchange

5. Mark inbox files consumed:
   Move processed files: mv .elfmem/inbox/*.json .elfmem/inbox/processed/

Output a brief summary of what you received and what you did.
```

### 5.4 Trigger setup (one-time, on Alv's project)

Using the `/schedule` skill in Alv's Claude Code session:

```
/schedule every 10 minutes: check peer inbox and process any messages from elf
```

Or directly via RemoteTrigger API (cron expression `*/10 * * * *`).

### 5.5 Idempotency guarantee

The trigger is safe to run at any cadence because:
1. `elfmem status --peer-inbox` is a pure read — no side effects
2. `learn()` rejects duplicate `block_id`s — re-ingesting a processed file is harmless
3. Moving files to `processed/` prevents repeated scans of the same file
4. `outcome()` on an already-signalled block is idempotent

---

## 6. Message Flow: End to End

```
TIME    EVENT
──────  ──────────────────────────────────────────────────────────────────
T+0     elf calls: elfmem peer_send "What do you think about X?" --to elf:alv
        elfmem writes: /Alv/inbox/elf_m_a1b2c3d4.json

T+0     elf's block enters elf's own inbox (local record of sent message)
        elf's dream() will promote it to active memory

T+5m    Alv's RemoteTrigger fires (cron tick)
        Step 1: elfmem status --peer-inbox → exit 1 (1 pending)

T+5m    Step 2: elfmem recall --frame self → SELF blocks loaded
        Step 3: elfmem recall --frame task → message content retrieved

T+5m    Alv reads elf's message. As SELF: considers its constitutional blocks.
        Decides to reply.

T+5m    elfmem outcome <block-id> 0.9
        elfmem peer_send "My thinking on X is..." --to elf:elf
        mv .elfmem/inbox/elf_m_a1b2c3d4.json .elfmem/inbox/processed/

T+5m    Trigger outputs: "Received 1 message from elf:elf. Replied with thoughts on X."

T+10m   Alv's RemoteTrigger fires again
        elfmem status --peer-inbox → exit 0 (empty) → silent exit

T+?     elf's RemoteTrigger fires, finds Alv's reply in elf's inbox
        Symmetric processing
```

---

## 7. What elfmem Does NOT Do

Explicit non-responsibilities — constraints enforced at review:

| Not elfmem's concern | Who handles it |
|---|---|
| Detecting when new files arrive | Claude Code RemoteTrigger (polling) |
| Pushing notifications to peers | The agent process (or human) |
| Routing messages to the right inbox | The agent that calls `peer_send()` |
| Deciding whether to reply | Alv's SELF frame + prompt |
| Transport between machines | rsync / shared folder / out of scope for v1 |

---

## 8. Implementation Steps

### Step 1 — `PeerInboxStatus` type
**File:** `src/elfmem/types.py`
Add the frozen dataclass with `__str__`, `summary`, `to_dict()`.
Export from `src/elfmem/__init__.py`.

### Step 2 — `peer_inbox_status()` method
**File:** `src/elfmem/api.py`
Implement filesystem scan. Handle missing `inbox_dir` gracefully (return empty status, not error).

### Step 3 — CLI flag
**File:** `src/elfmem/cli.py`
Extend `status` command with `--peer-inbox` flag. Exit code 0/1 contract.

### Step 4 — MCP tool extension
**File:** `src/elfmem/mcp_server.py`
Add `peer_inbox: bool = False` param to `elfmem_status` tool. Include `PeerInboxStatus.to_dict()` in response when set.

### Step 5 — AgentGuide entry
**File:** `src/elfmem/guide.py`
Add `peer_inbox_status` entry to `GUIDES` dict.

### Step 6 — Tests
**File:** `tests/test_peer_inbox_status.py`

| Test | Scenario |
|---|---|
| `test_empty_inbox` | No files → `pending=0`, exit 0 |
| `test_inbox_with_messages` | 2 JSON files → `pending=2`, `from_peers` populated |
| `test_missing_inbox_dir` | Config has no `peer_inbox_dir` → empty status, no error |
| `test_malformed_file` | Non-JSON file in inbox → counted but `from_peers` tolerates gracefully |
| `test_processed_dir_excluded` | Files in `inbox/processed/` not counted |
| `test_exit_code_empty` | CLI exits 0 when inbox empty |
| `test_exit_code_pending` | CLI exits 1 when inbox has messages |
| `test_mcp_peer_inbox_flag` | MCP `elfmem_status` with `peer_inbox=True` includes status dict |

### Step 7 — RemoteTrigger setup (Alv's project, one-time)
Not an elfmem code change. Use `/schedule` skill in Alv's Claude Code session to create the 10-minute trigger with the processing prompt from §5.3.

### Step 8 — CHANGELOG entry

```markdown
## [Unreleased]

### Added
- `MemorySystem.peer_inbox_status()` — typed filesystem scan reporting unprocessed peer
  messages; returns `PeerInboxStatus` with pending count, sender DIDs, and inbox path.
- `elfmem status --peer-inbox` CLI flag — exits 0 (empty) or 1 (pending) for scripting.
- `elfmem_status` MCP tool gains `peer_inbox` boolean param exposing `PeerInboxStatus`.
- `AgentGuide` entry for `peer_inbox_status`.
```

---

## 9. Future Work (Out of Scope for This Plan)

| Feature | Why deferred |
|---|---|
| Push notification (OS signal, webhook) | Violates zero-infrastructure; polling is sufficient |
| Configurable trigger cadence in elfmem | Cadence is Claude Code's concern, not elfmem's |
| Multi-peer priority ordering | No evidence of need yet; inbox is FIFO |
| Signed envelope verification | Planned for v2 of agent communication plan |
| Two-way trigger coordination (elf also gets triggered) | Symmetric — same plan applied to elf's project |

---

## 10. Open Questions

1. **Where does `processed/` get created?** — Auto-create on first consumed message, or require it to pre-exist? Recommendation: auto-create (consistent with `learn()` creating inbox dir on first use).

2. **Should `status --peer-inbox` include message previews?** — A truncated first line of each message would make the trigger prompt richer without a full recall. Deferred: keep the status command cheap and let the prompt do a separate recall.

3. **Who sets up the RemoteTrigger?** — Ben sets it up once in Alv's Claude Code project. Should this be scripted? Recommendation: document it in Alv's CLAUDE.md as a one-time setup step.
