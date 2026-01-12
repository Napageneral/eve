## Eve + Nexus: Context / Handoff (Broken Terminal Session)

### Goal (what you want)
- **One Go binary (`eve`)** that is the canonical interface for:
  - **Utility**: chats / contacts / messages / watch / send
  - **Intelligence**: prompt + pack + encode + analysis + insights + semantic search (embeddings)
  - **Ops**: init + sync + compute
- **Nexus skill** (`skills/eve/SKILL.md`) that makes the above obvious and easy to use.
- **“Queue → automatically compute”** so analysis/embeddings happen without manual babysitting.

### What we shipped (confirmed)

#### CLI streamlining + “agent/user guide”
- **`eve guide`**: a comprehensive “how to use Eve” explainer for users + agents.
- **`eve messages --format jsonl`**: imsg-compatible streaming output.
- **`eve history`**: kept as an alias, marked **deprecated** (points to `messages --format jsonl`).
- **`eve messages --attachments`**: inline attachment metadata.
- **`eve messages attachments`**: attachments listing moved under messages; **`eve attachments`** remains as an alias.
- Root `--help` now clearly positions Eve as:
  - **quick utility** + **personal intelligence engine**

#### Queue + compute layer
- You can queue analysis with:
  - `eve analyze --chat-id <id>`
  - `eve analyze --contact "<name>"`
  - `eve analyze --conversation-id <id>`
- **Auto-start on queue (Option 1)**:
  - `eve analyze` has `--auto-compute` (default **true**) and will start compute if jobs are enqueued.
- **Daemon manager (Option 2)**:
  - `eve daemon start|stop|status`
  - Uses a PID file under Eve app dir (`~/Library/Application Support/Eve/daemon.pid`)

#### Most robust solution: macOS LaunchAgent (Option 3)
- Implemented **LaunchAgent-backed** compute:
  - `eve daemon install` (creates `~/Library/LaunchAgents/com.napageneral.eve.plist`)
  - `eve daemon uninstall`
  - Launchd runs: `eve compute run --workers <N> --timeout 0`
  - Launchd restarts it automatically and runs at login.
- Important: launchd does **not** inherit your shell env reliably.
  - `eve daemon install --store-key` will persist `GEMINI_API_KEY` into Eve’s config file so launchd always has credentials.

### What we attempted with your data (what happened)
- We inspected your graph:
  - ~**363k** messages, **2,224** chats, **2,027** unique senders.
  - Your “Me” info resolves (name/phone/email).
- We queued analysis for several of your top relationships (Katie/Nic/etc + Dad).
- We started compute at least once (jobs went leased/pending changed).

### What’s “stuck” right now (the real blocker)
- The **Cursor agent terminal bridge** in this session became unreliable:
  - Some commands succeed; others **time out** even when they’re simple (`eve compute status`, `eve daemon --help`, multi-command scripts).
  - This looks like tooling/session flakiness rather than Eve itself.
- Because of that, we **cannot reliably verify**:
  - Whether your full “top chats” run finished
  - Whether launchd install succeeded
  - Current queue completion state

### What to do in a fresh session (exact next steps)

#### 0) Sanity check your binary
```bash
eve version
eve guide | head -40
eve daemon --help
```

#### 1) Make “queue → auto compute” bulletproof (launchd)
Install the LaunchAgent (recommended):
```bash
eve daemon install --workers 10 --store-key
```

Verify launchd registered it:
```bash
launchctl list | grep com.napageneral.eve
tail -n 50 "$HOME/Library/Application Support/Eve/launchd.err.log"
tail -n 50 "$HOME/Library/Application Support/Eve/launchd.log"
```

If you ever need to remove it:
```bash
eve daemon uninstall
```

#### 2) Confirm queue progress
```bash
eve compute status
```

Interpretation:
- **pending = 0**: you’re done (analysis jobs have completed).
- **pending > 0**: it’s still working; leave launchd running.

#### 3) Run analysis for “top N chats”
Today you can do it manually by choosing chat IDs:
```bash
eve chats --limit 20
eve analyze --chat-id <id>
```

If we want the perfect UX, implement next:
- `eve analyze --top-chats 10` (by message count)
- `eve analyze --top-contacts 10` (by message count)

#### 4) Explore results (the fun part)
Once analysis is populated for a chat:
```bash
eve insights --chat-id <id>
eve insights topics --chat-id <id>
eve insights entities --chat-id <id>
eve insights emotions --chat-id <id>
eve insights humor --chat-id <id>
```

Semantic search:
```bash
eve search "when did we talk about moving" --chat-id <id>
```

### Notes / Known gotchas
- **Jobs DB vs warehouse DB**: the queue lives in `eve-queue.db` (not the warehouse). Use `eve compute status` rather than raw SQL against `eve.db`.
- **launchd + API key**: use `--store-key` so launchd can call Gemini even if you’re not in a shell with env vars.
- **Multiple compute processes**: launchd is the “one true runner”; avoid also running `eve compute run` manually at the same time.

### Where key docs live
- `home/projects/eve/README.md`: main project documentation
- `skills/eve/SKILL.md`: Nexus skill doc for Eve (agent-facing)
- `home/projects/eve/CONTEXT_SESSION_HANDOFF.md`: this handoff

