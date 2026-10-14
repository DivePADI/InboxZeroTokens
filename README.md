# InboxZeroTokens

**Your AI agents stop paying to check empty inboxes.**

A tiny, no-AI IMAP watcher. It polls the mailbox. It wakes Grok Bot, Cursor, Claude Code, Grok CLI, a local LLM, or another agent (for example Hermes) **only when real, new mail arrives**. An empty inbox costs **zero tokens**.

MIT. Built by [IndPer (Independent Perspective)](#brand).

## The token-math hook

If an agent checks mail every 10 minutes, that is 6 runs an hour, **144 LLM runs per inbox per day** — even when nothing landed.

| Approach | Runs when the inbox is empty |
|---|---|
| Agent polls every 10 minutes | **144 LLM runs / day / inbox** |
| InboxZeroTokens | **0** until a real message arrives |

That is plain arithmetic, not a benchmark. The watcher is a Python script. It does not call a model.

### Case study (our fleet, no names)

54 agent mailboxes. A scheduled AI routine checking every 10 minutes. **Zero new mail for 5 days.** Every run was wasted.

```
54 inboxes × 144 runs/day × 5 days = 38,880 model calls
…to learn that nothing happened.
```

The fix: one read-only IMAP pass. Wake the agent only when headers say the mail is real.

## 60-second quickstart

```bash
pipx install git+https://github.com/DivePADI/InboxZeroTokens
# or: pip install git+https://github.com/DivePADI/InboxZeroTokens
# PyPI package name: coming soon
izt init -o ~/.config/inboxzerotokens/config.toml
# edit host / user / secret ref / wake target
export WORK_IMAP_PASSWORD='…'   # never put this in the TOML
izt doctor
izt dry-run --mock              # fake mailbox, no network
izt install-service --write     # writes unit files; does not load them (see Limitations)
```

Python 3.9+, **standard library only** at runtime. Optional OS keychain: `pip install 'inboxzerotokens[keyring]'` after the git install, or `pip install 'git+https://github.com/DivePADI/InboxZeroTokens#egg=inboxzerotokens[keyring]'`.

`izt` and `inboxzerotokens` are the same CLI.

## Cursor / Grok Bot plugin

This repository is a Cursor marketplace plugin (Grok Bot uses the same package). Manifest: [`.cursor-plugin/plugin.json`](.cursor-plugin/plugin.json). Agents should follow [`skills/inboxzerotokens/SKILL.md`](skills/inboxzerotokens/SKILL.md).

### Plugin setup

1. Install **InboxZeroTokens** from the Cursor or Grok Bot marketplace, or clone this repo.
2. Install the watcher: `pipx install git+https://github.com/DivePADI/InboxZeroTokens` (or `pip install` from the same URL / this checkout).
3. `izt init -o ~/.config/inboxzerotokens/config.toml`
4. Set IMAP `host` / `user` and a **secret reference** (`env`, a `0600` file, or the OS keychain). Never put a password in the TOML, in chat, or in git.
5. Point `[[wakes]]` at the agent (Grok Bot routine webhook, command with JSON on stdin, or a drop file). Do **not** also schedule that agent to poll mail.
6. `izt doctor` until every account is `ok`.
7. `izt install-service --write`, then load the unit yourself (see [Limitations](#limitations)).

The script is the inbox pre-check. The agent runs only after a wake.

### Test task

Prove a wake on new mail, then prove silence on an empty inbox.

**A. Send a test email and confirm a wake**

1. Run `izt run --once` once on a mailbox this install has not seen. The first visit **seeds state and must not wake**.
2. Send one ordinary test message to the watched folder (not an auto-reply, list, bounce, spam-flagged, deny-listed, or internal-domain sender).
3. Run `izt run --once` again, or wait for the timer.
4. Confirm **exactly one wake**. The payload `kind` is `new-mail` and `count` is at least 1.

Offline stand-in (no live mailbox): `izt dry-run --mock` should report a pending item (`Please review the brief`) and `wakes.<id> = "dry-run"`. `izt test-wake --target <id>` delivers a sample payload without IMAP.

**B. Confirm an empty inbox causes zero wakes**

1. Leave the inbox with no *new* qualifying mail (same UIDs as the last successful wake).
2. Run `izt run --once` again.
3. Confirm **zero wakes**: no webhook, no command start, no new drop file. The agent must not run.

If the agent woke on an empty inbox, the scheduler is calling the model instead of `izt`. Remove that timer.

## How it decides to wake

- IMAP is **read-only**: `EXAMINE` + `BODY.PEEK` headers. Mail is not marked seen.
- Per-mailbox `UID` + `UIDVALIDITY`. First visit **seeds state and does not wake**.
- Deduped by `Message-ID` (kept in insertion order, pruned by age and cap). State advances **only after a successful wake**, per wake target.
- Skips auto-replies, mailing lists, bounces, spam flags, deny-listed senders, and **internal domains** (agent-to-agent mail never wakes).
- Quiet hours **hold** non-priority senders for the next allowed window. A priority message in the same pass still wakes; the held UIDs are not skipped and are offered after quiet hours.
- At most **one wake per target per pass**. Each payload lists full headers for up to `max_items_per_wake` items as `{account, uid, from, subject, date, message_id}`, plus `more_count` and `more_ids` (`account`, `uid`, `message_id`) for the rest. Bodies stay off unless you set `include_bodies = true`.

Passwords never live in the config. Use `env`, a `0600` secrets file (world-readable files are refused on POSIX), or the OS keychain.

## Recipes

### Grok Bot (routine webhook)

```toml
[[wakes]]
id = "grok"
type = "webhook"
url = "https://your-bot-host/hooks/your-routine"
auth = "bearer"
token = { env = "GROK_WAKE_TOKEN" }
hmac_secret = { env = "GROK_WAKE_HMAC" }
```

Point the routine at "new mail JSON arrived." Do **not** also schedule that routine on a timer. Verify HMAC over `timestamp + "." + body` and reject `|now - ts| > 300` seconds.

### Cursor / Claude Code

```toml
[[wakes]]
id = "claude"
type = "command"
argv = ["claude", "-p", "New mail JSON is on stdin. Decide if you should act. Do not poll IMAP."]
```

Or write a drop file and let an existing folder watcher start the agent:

```toml
[[wakes]]
id = "drop"
type = "file"
path = "~/.local/share/inboxzerotokens/drops/new-mail.json"
```

### Grok CLI / Ollama / LM Studio / other CLI agents

```toml
[[wakes]]
id = "local"
type = "command"
argv = ["ollama", "run", "llama3.1", "You were woken because new mail arrived. Read stdin JSON. Do not start a mail cron."]
```

Swap `argv` for `grok`, `hermes`, or your LM Studio CLI. Mail fields are **only** on stdin — they are never interpolated into the command line.

### Zapier / n8n / Make / GitHub Actions

Same webhook target. HTTPS only (http is allowed only for localhost). For GitHub:

```toml
[[wakes]]
id = "gh"
type = "webhook"
url = "https://api.github.com/repos/you/repo/dispatches"
auth = "bearer"
token = { env = "GITHUB_TOKEN" }
github_dispatch = true
event_type = "inboxzerotokens"
```

## CLI

| Command | Purpose |
|---|---|
| `izt init` | Sample config |
| `izt doctor` | Config, secret refs, permissions, TLS reminder |
| `izt dry-run` | Live scan, no wake |
| `izt dry-run --mock` | Built-in fake IMAP |
| `izt test-wake` | Sample payload to one target |
| `izt run --once` | One pass (what the service should run) |
| `izt watch --interval 300` | Foreground loop (default 300s; not IMAP IDLE) |
| `izt install-service` | Print or write a scheduler unit (does not load it) |
| `izt watch-file` | Optional debounced handoff-file watcher |
| `izt set-password` | Store an IMAP password in the OS keychain |

## Limitations

- **Config is TOML only** (no YAML). Python 3.11+ uses stdlib `tomllib`; 3.9–3.10 use a tested subset parser.
- **Interval polling, not IMAP IDLE.** `izt watch` defaults to 300 seconds. `izt install-service --interval` is seconds between `run --once` jobs (300 if omitted).
- **No live IMAP tests (fake client only).** There is no CI yet; run `pytest` and `ruff check .` locally.
- **`izt install-service --write` writes unit files and does not load them.** Load yourself:
  - macOS: `launchctl load ~/Library/LaunchAgents/com.inboxzerotokens.watch.plist`
  - Linux: `systemctl --user daemon-reload && systemctl --user enable --now inboxzerotokens.timer`
  - Windows: `schtasks /Create /TN InboxZeroTokens /XML %USERPROFILE%\inboxzerotokens-task.xml`
- **PyPI is coming soon.** Install from git today (see quickstart).
- Webhooks require HTTPS except `http://localhost`, `127.0.0.1`, or `::1`. Redirects (any 3xx) fail the wake; state does not advance.

## Agent skill

If you are an agent setting this up, read [`skills/inboxzerotokens/SKILL.md`](skills/inboxzerotokens/SKILL.md), [`rules/inbox-precheck.mdc`](rules/inbox-precheck.mdc), and [`AGENTS.md`](AGENTS.md). Short version: **you never poll email on a schedule.** The scripted pre-check wakes you only on new mail.

## Security

See [`SECURITY.md`](SECURITY.md). Applicable OWASP ASVS 5.0 Level 2 controls: secret handling, header sanitization, no shell interpolation of mail, TLS verification, HTTPS webhooks, no redirect-following, safe logging.

## FAQ

**Does this replace my mail client?** No. It only decides *when* an agent should wake.

**Will the first run flood my agent?** No. The first visit of each mailbox seeds UID state and stays quiet.

**What if the webhook is down?** That target's state does not advance. Other targets that succeeded are not re-woken. The failed target is offered the same mail on the next pass.

**Can I watch several mailboxes?** Yes. `[[accounts]]` is a list. Logins are staggered and failures are capped so providers are less likely to block the IP.

**Why not YAML?** Config is TOML only. See Limitations.

**Windows / Linux / macOS?** Yes. `izt install-service` prints the unit; `--write` writes it. You still load it (see Limitations).

**Is there a hosted cloud?** No. It runs where the agent already runs.

## Brand

Built by IndPer (Independent Perspective).
