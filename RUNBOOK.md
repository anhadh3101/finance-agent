# Runbook: Personal Finance Memory Agent

> **This plan can change.** It is a starting point, not a contract. If the user's instructions differ from anything in this runbook, follow the user and update this file to match.

**Event:** Data and AI Hackathon, Sep 11, 2026, AWS Builder Loft
**Build time:** 3 hours
**Goal:** Meet the basic requirements: all five sponsor tools doing real, repeated work, plus a clean Snyk scan. Finish on time. Winning is not the goal.

---

## 1. What we're building

A spending coach that remembers you. The user opens the Finance Agent dashboard at `/dashboard`, links a public Google Sheet of transactions, and sets one savings goal. Telegram credentials live in `.env` on the server. On each run (a button click or a timer), the agent does four things:

1. Checks the sheet for new transactions.
2. Compares spending against budgets and the goal.
3. Remembers what the user has told it before.
4. Sends a Telegram message: a nudge if something needs attention, a short "on track" note if not, or nothing at all if no new data came in.

The user replies in Telegram, for example "keep Hulu, it's shared with my roommate", and the agent respects that on later runs. Once a run succeeds, its workflow is captured and replayed on later runs, so those runs cost less.

---

## 2. Decisions already made

| Area | Decision |
|---|---|
| Backend | FastAPI (Python) — serves the API, dashboard, agent loop, and Swagger UI |
| Frontend | Dashboard at `localhost:8000/dashboard`; Swagger at `/docs` for debugging |
| Login | None. Memory is keyed by Telegram `chat_id` from `.env`. |
| Transaction source | Public Google Sheet with a `transactions` tab and a `budgets` tab |
| Notifications | Telegram bot. Token and chat ID live in `.env`, never in the dashboard or `config.json`. |
| Budgets | Live only in the sheet's `budgets` tab. Category limits are not in the web form. |
| Goals | One active goal at a time, saved in `config.json`: a text **goal** description plus a **target_amount** (minimum bank balance when the goal is complete). |
| Agent | **Claude Code**, triggered headlessly. The `finance-run` skill is the workflow; all data access happens in `backend/finance_cli.py`, so no secrets enter the model's context. |
| Scheduling | "Run now" via `POST /api/run`, plus an optional auto-run every 2 minutes |
| Rote success rule | Default: the run completed and the Telegram message was delivered. Stretch goal: the nudged category's spending dropped afterward. |

**Not RocketRide.** An earlier draft of this plan used RocketRide as the agent
platform. The agent is now a Claude Code skill. `.rocketride/` and
`pnpm-workspace.yaml` are leftovers from that draft and are not used.

**Goal example:** "Save $10K for a Japan trip in 2 months" with `target_amount = 60000` means the user wants at least $60K in the bank when done — $10K for the trip on top of a $50K cushion.

---

## 3. Verify first (T+0:00 to T+0:30)

These are the open questions that can sink the build. Test each one in 5 minutes or less. Write the answers down here, because they decide which fallbacks from section 16 apply.

- [x] **FastAPI scaffold.** `cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && uvicorn main:app --reload` — confirm `/health`, `/dashboard`, and `/docs` load.
- [x] **Telegram setup.** Bot token and chat ID in `.env`. Message the bot once, then run `python fetch_chat_id.py` or `POST /api/find-chat-id` to capture the chat ID.
- [x] **Sheet download.** 30 rows load and clean; as-of date is the latest row. The sheet
      currently has **no `budgets` tab**, so the built-in limits are used and a warning
      is reported. Add a `budgets` tab to fix this.
- [x] **hotdata API shape.** `hotdata==0.10.0` Python SDK. Inline CSV load via
      `LoadManagedTableRequest(data=..., format="csv", mode="replace")` avoids the
      upload step entirely for our size. Queries via `QueryApi.query` with
      `X-Database-Id`. Dialect is DuckDB-flavoured Postgres: `date_trunc` works, but
      `asof` is a reserved word.
- [x] **HydraDB round trip.** `context.ingest` with `upsert="true"` plus
      `context.list(ids=[...])` and `context.inspect` work. No Cypher surface in this
      SDK version — see section 8.
- [x] **Cognee.** `add` / `cognify` / `search` work with `LLM_API_KEY`. `cognify` takes
      about 50 seconds, so it runs after the Telegram send. It logs a banner to stderr
      on import; `LOG_LEVEL=ERROR` silences it.
- [ ] **hotdata key.** `HOTDATA_API_KEY` is still blank. Get it from the booth. Until
      then set `ALLOW_LOCAL_QUERY_FALLBACK=true`.
- [ ] **Claude Code sign-in.** `claude` reports "OAuth session expired". Run `claude`
      in a terminal, sign in, and accept the trust prompt in the repo root.
- [ ] **Rote:** How does it capture a run: by watching API calls, or by wrapping commands? How do we trigger a replay? Can it reach our endpoints and the Telegram API?
- [ ] **Snyk:** Run `snyk auth`, then confirm `snyk test` and `snyk code test` work on the repo.

**Rule:** If a tool is still broken at T+0:30, go to that sponsor's booth right away. Don't debug alone.

---

## 4. Timeline

This assumes one person. The FastAPI scaffold, dashboard, and Telegram wiring are already present.

| Time | Work | Checkpoint |
|---|---|---|
| 0:00 to 0:30 | Verify list (section 3). In parallel: build the seed sheet (section 6). | Every tool answers one call |
| 0:30 to 1:15 | Core run as a plain script: sheet to hotdata to queries; goal to Cognee to HydraDB; message to Telegram | **A real Telegram message arrives** |
| 1:15 to 1:40 | Wire remaining endpoints; test full flow from dashboard and Swagger | Dashboard save + run works |
| 1:40 to 2:05 | Agent decision logic in Python (`agent.py`): flags, memory, LLM nudge text | A nudge message arrives with goal context |
| 2:05 to 2:25 | Telegram replies to Cognee to HydraDB, run summaries, `last_row` tracking | "Keep Hulu" test passes |
| 2:25 to 2:45 | Rote capture and replay, run log | Run log shows a "replay" row |
| 2:45 to 3:00 | Snyk scan and fixes, one full rehearsal (section 18) | Submitted |

**Hard rule:** Stop adding features at 2:45, whatever state things are in.

---

## 5. Architecture

```
Browser (dashboard at localhost:8000/dashboard)
                        |
                        v
               FastAPI (localhost:8000)
   /api/config  /api/find-chat-id  /api/run  /api/autorun  /api/state
                        |
          reads .env (secrets) + config.json (sheet + goal)
                        |
                        v
   POST /api/run  ->  claude -p "/finance-run" --output-format json
                        |
                        v
        .claude/skills/finance-run/SKILL.md  (the workflow)
          the only three commands it may run:
                        |
    +-------------------+--------------------+
    |                   |                    |
    v                   v                    v
finance_cli.py     finance_cli.py       finance_cli.py
   context             send                 record
    |                   |                    |
    |                   v                    v
    |            Telegram sendMessage   HydraDB writes (run state,
    |                                   nudge, preferences)
    |                                   + Cognee run summary
    v
  Google Sheet CSV -> hotdata tables (replace) -> Q1-Q4 SQL
  HydraDB          -> goal, last_row, preferences, recent nudges
  Telegram         -> getUpdates (new replies)
```

The split matters: every number is computed in Python and every credential stays in
`.env`. Claude Code only decides which flags survive the user's preferences and writes
the message. It cannot read `.env` or run any other command.

### What each tool reads and produces

| Tool | Reads | Produces |
|---|---|---|
| **Google Sheet** | Transactions the user adds | CSV downloaded on every run |
| **hotdata** | `transactions` and `budgets` tables loaded from the CSV | Spend vs. budget, new rows, large charges, new subscriptions |
| **Cognee** | Words only: goal description, Telegram replies, run summaries | Entities and relationships (preferences, patterns, goal links) |
| **HydraDB** | Structured goal fields, run state, preferences, nudges | Goal, `last_row`, `tg_offset`, preferences, recent nudges, run log |
| **Claude Code** | The `context` JSON only | Which flags survive, the message text, the preferences to save |
| **Rote** | A successful agent run | A replayable routine used on later runs |
| **Snyk** | The repo | A vulnerability report, which we fix before submitting |

**Boundary rule:** Raw transaction rows never go to Cognee, and text never goes to hotdata. The only link between them is the run summary, which our code writes from hotdata's results.

---

## 6. Data contracts

### Local config (`config.json`, gitignored)

Saved from the dashboard via `POST /api/config`. One goal at a time — saving replaces the previous goal.

```json
{
  "sheet_url": "https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
  "transactions_gid": "0",
  "budgets_gid": "0",
  "goal": {
    "goal": "Save $10K for a Japan trip in 2 months",
    "target_amount": 60000
  }
}
```

| Field | Meaning |
|---|---|
| `goal.goal` | Free-text description of what the user is working toward |
| `goal.target_amount` | Minimum bank balance when the goal is complete. If the user is saving $10K on top of a $50K cushion, this is `60000`. |

Telegram credentials are **not** stored here. They live in `.env` only.

### Google Sheet (public, "anyone with the link can view")

**Tab `transactions`.** The header row must match exactly:

```
date,merchant,amount,category
2026-08-03,DoorDash,32.50,food_delivery
2026-08-04,Planet Fitness,25.00,gym
```

- `date` in `YYYY-MM-DD` format. `amount` as a plain number; the backend strips `$` and `,` anyway.
- Categories: `food_delivery, groceries, dining, gym, subscriptions, transport, shopping`.
- **Append only.** Never insert rows in the middle, because new-row detection uses row order.

**Tab `budgets`:**

```
category,monthly_limit
food_delivery,200
groceries,400
dining,150
gym,25
subscriptions,40
transport,120
shopping,150
```

**Tab `demo_rows`.** The app never reads this tab. Copy these batches into `transactions` during the demo:

- **Batch A:** four DoorDash or Uber Eats orders totaling about $120, plus `Hulu 15.99 subscriptions`, a merchant never seen before
- **Batch B:** three more delivery orders totaling about $90, plus normal groceries
- **Batch C:** `Best Buy 480.00 shopping`, a large charge far above typical

### Seed history

Put 5 to 6 weeks of rows in `transactions`, dated Aug 1 to Sep 7, 2026, with these patterns built in:

- Delivery rising week over week: about $40, $55, $70, $90
- Gym at $25 once a month
- Netflix at $15.49 once a month (an existing subscription, so it's not flagged)
- One Costco charge of $300 in late August
- Normal groceries and transport

### Dates

The "as-of" date is the **latest transaction date in the sheet**, not today's date. This keeps month-to-date math sensible with seeded data.

### CSV download

Never fetch a URL the user typed. Extract the sheet ID with a regex, check that the host is `docs.google.com`, then build the export URL ourselves:

```
https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={TAB_GID}
```

The dashboard asks for the sheet URL. Tab gids default to `0` unless we add advanced fields later.

---

## 7. hotdata

### Loading, on every run (`/tools/refresh-data`)

1. Download both tabs as CSV.
2. Validate the headers. On a mismatch, return a readable error such as "Column `amount` is missing from the transactions tab."
3. Clean: strip `$` and `,` from amounts, parse dates, add `row_num` numbered 1..N in sheet order.
4. Load both files into hotdata as `transactions` and `budgets`, **replacing** the tables each time. Reloading the full sheet stays in sync even if someone edits an old row.

### Queries

These are written in standard SQL; adjust them to hotdata's dialect after the verify step. `{last_row}` comes from HydraDB and is always passed through `int()` before being placed in the query, so it can't be used for injection.

**Q1: month-to-date spend vs. budget**

```sql
WITH asof AS (SELECT MAX(CAST(date AS DATE)) AS d FROM transactions)
SELECT t.category,
       SUM(t.amount)   AS spent,
       b.monthly_limit AS monthly_limit
FROM transactions t
JOIN budgets b ON b.category = t.category
CROSS JOIN asof
WHERE date_trunc('month', CAST(t.date AS DATE)) = date_trunc('month', asof.d)
GROUP BY t.category, b.monthly_limit;
```

The expected pace is `monthly_limit * day_of_month(as_of) / days_in_month`, computed in Python.

**Q2: new rows since the last run**

```sql
SELECT * FROM transactions WHERE row_num > {last_row} ORDER BY row_num;
```

**Q3: large charges among the new rows**

```sql
SELECT n.row_num, n.merchant, n.amount, AVG(h.amount) AS typical, COUNT(h.amount) AS seen
FROM transactions n
LEFT JOIN transactions h ON h.merchant = n.merchant AND h.row_num <= {last_row}
WHERE n.row_num > {last_row}
GROUP BY n.row_num, n.merchant, n.amount
HAVING (COUNT(h.amount) > 0 AND n.amount > 3 * AVG(h.amount))
    OR (COUNT(h.amount) = 0 AND n.amount > 200);
```

**Q4: new subscriptions**

```sql
SELECT n.merchant, n.amount
FROM transactions n
WHERE n.row_num > {last_row}
  AND n.category = 'subscriptions'
  AND n.merchant NOT IN (SELECT merchant FROM transactions WHERE row_num <= {last_row});
```

Stretch goal: detect recurring charges by amount and interval instead of by category.

---

## 8. HydraDB records

The installed `hydradb-sdk` exposes a memory-record API (`context.ingest`,
`context.list`, `context.inspect`), not raw Cypher. Each record has a stable
`source_id` we choose, free text that HydraDB infers over, and structured
`additional_metadata` we read back verbatim. `backend/hydra.py` wraps it.

| `source_id` | Metadata | Written when |
|---|---|---|
| `active-goal` | `type`, `goal_name`, `target_amount` | `POST /api/config` |
| `agent-run-state` | `type`, `state` (JSON: `last_row`, `tg_offset`, `runs[]`) | every `record` (mirror) |
| `preference-<kind>-<target>` | `type`, `kind`, `target`, `reason`, `recorded_at` | a reply is interpreted |
| `nudge-<run_id>` | `type`, `run_id`, `flag_types`, `categories`, `sent_at` | a message is sent |

Preferences and nudges each get a fresh `source_id`, so they are creates, not updates.
Reads are by `source_id` or by prefix over the listing, so no user text is ever
concatenated into a query.

### Where `last_row` actually lives

**In `run_state.json` at the repo root, not HydraDB.** During the build, writing run
state to HydraDB and immediately reading it back returned the *previous* value, and it
had not converged after 14 seconds. That is acceptable for memory but not for
`last_row`, which decides whether a transaction has already been nudged about — a stale
read means a duplicate message or a missed one.

So `backend/run_state.py` owns the authoritative copy in a local JSON file, and
`hydra.mirror_run_state` still writes the same data to HydraDB so the run log sits with
the rest of the memory and shows up in the dashboard. The mirror is best-effort; if it
fails, the run still succeeds and the warning is reported.

`run_state.json` is gitignored. To rehearse the demo from a clean baseline:

```bash
backend/.venv/bin/python backend/finance_cli.py reset
```

---

## 9. Cognee

### What goes in

| Input | When | Dataset |
|---|---|---|
| Goal description (`goal.goal`) | On `/api/config` save | `user_{chat_id}` |
| Telegram replies | At the start of each run | `user_{chat_id}` |
| Run summary | At the end of each run | `user_{chat_id}` |

```python
import cognee
await cognee.add(text, dataset_name=f"user_{chat_id}")
await cognee.cognify(datasets=[f"user_{chat_id}"])
```

These are the standard calls. Use whatever the installed version documents.

### Run summary template

This is written by our code from hotdata results:

```
Run on {as_of}: {n} new transactions.
Food delivery ${spent} month-to-date vs ${pace} expected (over budget).
New subscription: Hulu $15.99. Large charges: none.
Nudge sent about: food_delivery.
Goal: Save $10K for a Japan trip. Target bank balance: $60,000.
```

### The schema problem, and why it went away

The original worry was that Cognee's LLM invents its own relationship names, so it
could not be trusted to emit `IGNORES`, `PROTECTS`, or `KEEPS` for our queries to read.

That problem no longer applies, because the workflow itself does the interpreting.
Claude Code reads the raw reply text and calls
`record --preference "keep|Hulu|shared with roommate"`, which writes a preference to
HydraDB with exactly the `kind` and `target` our reads expect. Cognee still receives
every reply and run summary and builds its graph over them, but the agent's decisions
depend only on HydraDB's structured metadata. Cognee's graph is used for recall and
context, never as the source of a flag.

`cognify` calls an LLM and takes roughly 50 seconds, so it runs in the `record` step,
after the Telegram message has already been sent. Pass `--no-cognee` to skip it when
rehearsing the demo against the clock.

---

## 10. The agent run, step by step

`POST /api/run` takes a lock so two runs can't overlap, then shells out to
`claude -p "/finance-run"`. The workflow does the following.

**Step 1 — `finance_cli.py context`** (all in Python, one command):
- Downloads both sheet tabs, cleans them, numbers rows `1..N`.
- Replaces the `transactions` and `budgets` tables in hotdata.
- Reads `last_row` and `tg_offset` from HydraDB run state.
- Runs Q1-Q4 and computes candidate flags:
  - `overspend`: a category with new rows this run, month-to-date spend above
    1.2 × expected pace or above its monthly limit, **and** at least two charges this
    month. The charge-count rule stops a fixed monthly cost (gym, one subscription)
    from being flagged just because it lands on the 1st.
  - `large_charge`: 3 × the merchant's historical average, or over $200 for a merchant
    never seen before.
  - `new_subscription`: a `subscriptions` row whose merchant has no history.
- Fetches new Telegram replies and returns the advanced `tg_offset`.

**Step 2 — the model reads the replies.** Each reply becomes zero or more preferences
of kind `keep`, `protect`, or `ignore`. "Keep Hulu, it's shared with my roommate"
becomes `keep|Hulu|shared with roommate`.

**Step 3 — the model decides.** First match wins:
- `is_first_run` is true → mode `baseline`. One intro message covering month-to-date
  spending and the goal. Flags are ignored.
- `new_row_count` is 0 → mode `skipped`. **Nothing is sent.**
- Otherwise → mode `fresh`. Drop every flag covered by a preference, then write a
  nudge if any survive, or a short "on track" note if none do.

**Step 4 — `finance_cli.py send`.** Plain text, no `parse_mode`, four sentences max,
tied to the goal's `target_amount`. Skipped entirely on a `skipped` run.

**Step 5 — `finance_cli.py record`.** Always runs, so `last_row` advances even on a
skipped run. Writes run state, the nudge, and new preferences to HydraDB, then adds the
run summary and replies to Cognee. Cognee's `cognify` is slow, so it happens here,
after the user already has their message.

**Step 6 — the model reports** one JSON object, which `agent.py` parses into the
`/api/run` response.

---

## 11. The Claude Code workflow

The agent is a Claude Code skill at `.claude/skills/finance-run/SKILL.md`.
`backend/agent.py` launches it:

```bash
claude -p "/finance-run" --output-format json --max-turns 16 \
  --allowed-tools "Bash(backend/.venv/bin/python backend/finance_cli.py context:*)" \
                  "Bash(backend/.venv/bin/python backend/finance_cli.py send:*)" \
                  "Bash(backend/.venv/bin/python backend/finance_cli.py record:*)"
```

`disable-model-invocation: true` in the skill's frontmatter means it only ever runs
when triggered by name, never on its own initiative.

### One-time setup

The workflow cannot run until both of these are done once:

1. **Sign in.** Run `claude` in a terminal. If the session has expired, `/api/run`
   returns "Failed to authenticate: OAuth session expired".
2. **Trust the workspace.** Run `claude` once in the repo root and accept the trust
   prompt. Until then Claude Code ignores `.claude/settings.json`, which is why
   `agent.py` also passes `--allowed-tools` on the command line.

### `backend/finance_cli.py`

Three subcommands. This is the whole surface the workflow can touch.

| Command | Does |
|---|---|
| `context` | Reloads the sheet into hotdata, runs Q1-Q4, reads the goal and preferences from HydraDB and bookkeeping from `run_state.json`, fetches new Telegram replies. Prints one JSON document. |
| `send --text` | Sends one plain-text Telegram message. |
| `record --mode ...` | Writes the run state (`last_row`, `tg_offset`, run log), the nudge, and any new preferences, then adds the run summary to Cognee. |
| `reset` | Clears bookkeeping so the next run is a baseline. **Not** in the workflow's allow-list — resetting is a human decision. |

Errors are printed as `{"error": "..."}` on stdout so the workflow can read them.
Cognee's startup banner is silenced with `LOG_LEVEL=ERROR` so the JSON stays clean.

### Why the split

Claude Code never sees `.env`, never opens a socket itself, and never computes a
number. Pace math, flag thresholds, and SQL live in `analysis.py`. The model's job is
judgment: reading a Telegram reply as a preference, dropping flags the user has
already settled, and writing four sentences a person will act on.

**`/tools/*` endpoints** still exist for debugging through Swagger and require the
`X-Agent-Secret` header. The workflow does not use them; it calls the CLI directly.

---

## 12. Rote

**Status: not built.** Rote was the last item in the timeline and the first fallback to
drop. The hook for it is `agent.py`, which is the single place a run is dispatched.

**Role if it gets built:** capture the first successful nudge run and replay it later.

**Where it would go:** in `agent.py`, before launching Claude Code. Read the surviving
`flag_types` from a cheap `finance_cli.py context` call, and if a captured routine
covers that exact set, replay it instead of paying for a model turn. Log mode `replay`
by passing `--mode replay` to `record`.

**What counts as success:** the run completed and Telegram returned `ok: true`.
**Stretch goal:** mark a nudge `improved` when the category's spending in the 7 days
after the nudge is lower than in the 7 days before, then capture only improved runs.

**Expected run log for the demo** (the shape, not measurements):

| Run | Situation | Mode |
|---|---|---|
| 1 | Baseline | baseline |
| 2 | Delivery overspend and Hulu | fresh |
| 3 | Delivery overspend (Hulu is kept) | fresh, no Hulu mention |
| 4 | Large Best Buy charge | fresh |
| 5 | No new rows | skipped, nothing sent |

Runs 3 and 5 are the ones worth demoing: run 3 proves memory changed the output, and
run 5 proves the agent stays quiet when it has nothing to say.

---

## 13. FastAPI endpoints

**User-facing** (CORS allows configured local development origins; do not use `*`):

| Endpoint | Does |
|---|---|
| `GET /` | Redirects to `/dashboard` |
| `GET /dashboard` | Setup form UI |
| `GET /health` | Liveness check |
| `GET /api/config` | Returns saved sheet URL and goal from `config.json` |
| `POST /api/config` | Validates and saves sheet URL and goal to `config.json`. Writes the structured goal to HydraDB. Sends goal description to Cognee. |
| `POST /api/find-chat-id` | Calls Telegram `getUpdates` using `TELEGRAM_BOT_TOKEN` from `.env`, saves chat ID to `.env` |
| `POST /api/run` | Launches the `finance-run` workflow (section 10) and returns its mode, message, and flags |
| `POST /api/autorun` | Turns the 2-minute background loop on or off |
| `GET /api/state` | Returns the goal, preferences, run log, and last message |

**Debugging tools** (require the `X-Agent-Secret` header): `/tools/refresh-data`,
`/tools/spend-analysis`, `/tools/memory`, `/tools/send-telegram`. The workflow does not
call these; they exist so you can exercise one stage at a time from Swagger.

Telegram notes: if the bot ever had a webhook set, `getUpdates` returns a 409 error, so call `deleteWebhook` first (already done in `telegram.py`). A bot cannot message a user until that user has messaged the bot.

### Running locally

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env    # fill in keys + TELEGRAM_BOT_TOKEN
uvicorn main:app --reload --port 8000
```

Then, once, from the repo root: run `claude`, sign in, and accept the trust prompt.

Open `http://localhost:8000/dashboard` for setup. Use `http://localhost:8000/docs` to debug API calls.

To run the workflow without the server, from the repo root:

```bash
claude -p "/finance-run"
```

To inspect what the workflow sees, without sending anything:

```bash
backend/.venv/bin/python backend/finance_cli.py context
```

### Telegram chat ID setup

1. Put `TELEGRAM_BOT_TOKEN` in `.env`.
2. Message the bot once in Telegram.
3. Run `python fetch_chat_id.py` from `backend/`, or call `POST /api/find-chat-id`.
4. Restart uvicorn if it was already running so it picks up `TELEGRAM_CHAT_ID`.

---

## 14. Dashboard UI

The dashboard at `localhost:8000/dashboard` is the primary demo UI.

**Setup fields:**
1. **Google Sheet link** — public sheet with `transactions` and `budgets` tabs.
2. **Goal** — free-text description of what the user is working toward.
3. **Target amount** — minimum bank balance when the goal is complete.

**Agent panel:**
- **Run now** — calls `POST /api/run` and shows the message that was sent, or why none was.
- **Auto-run every 2 minutes** — toggles the background loop.
- **Memory** — the active goal and every learned preference.
- **Run log** — time, mode, new rows, flags, and whether a message went out.

**Not on the dashboard:** Telegram bot token and chat ID (configured in `.env` only).

**Also useful during the build:**
- Swagger at `/docs` for `POST /api/run`, `GET /api/state`, and tool endpoints.
- `GET /health` for a quick server check.

**Fallback if the dashboard falls behind:** Swagger at `/docs` can run every endpoint. Use it for the demo.

---

## 15. Repo layout and config

```
data-ai-hackathon/
  .claude/
    settings.json           # Tool permissions for the workflow (allow 3, deny .env)
    skills/finance-run/
      SKILL.md              # THE WORKFLOW — triggered with /finance-run
  backend/                  # FastAPI service
    main.py                 # FastAPI app, /api/* + /tools/*, autorun loop, run lock
    agent.py                # Launches `claude -p /finance-run`, parses its JSON
    finance_cli.py          # context / send / record — the workflow's only tools
    config.py               # Settings from .env
    store.py                # config.json read/write and validation
    sheet.py                # URL validation, CSV download, cleaning, row_num
    hotdata_client.py       # uploads, table loads (replace), SQL queries
    query_engine.py         # Picks hotdata, or local DuckDB as a labelled fallback
    analysis.py             # Q1-Q4 SQL, pace math, flag rules
    hydra.py                # Goal, preferences, nudges, run-log mirror
    run_state.py            # Authoritative last_row / tg_offset / run log (local JSON)
    memory.py               # Cognee add/cognify/search
    telegram.py             # sendMessage, getUpdates, deleteWebhook
    fetch_chat_id.py        # CLI: fetch chat ID and write to .env
    static/dashboard.html   # Setup form, Run now, run log, memory
    requirements.txt        # pinned versions
  config.json               # gitignored — sheet URL + goal (saved from dashboard)
  run_state.json            # gitignored — last_row, tg_offset, run log
  .env                      # gitignored — server secrets + Telegram credentials
  .env.example
  RUNBOOK.md
  transactions.csv          # local sample data matching the seed history
```

### If the hotdata key is not available yet

Set `ALLOW_LOCAL_QUERY_FALLBACK=true` in `.env`. The same SQL then runs in a local
DuckDB file so the rest of the agent can be built and demoed. It is never silent: the
`context` output carries `"engine": "duckdb-local"` and a warning, and the dashboard
shows it. Remove the flag as soon as `HOTDATA_API_KEY` is set — hotdata is a sponsor
requirement and the local engine does not satisfy it.

**`.env` (server secrets, never committed):**

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather token |
| `TELEGRAM_CHAT_ID` | User's chat ID (set via `fetch_chat_id.py`) |
| `HOTDATA_API_KEY` | hotdata access |
| `HOTDATA_DATABASE_ID` | Optional. Blank means find-or-create by `HOTDATA_DATABASE_NAME`. |
| `ALLOW_LOCAL_QUERY_FALLBACK` | Fallback only — run queries in local DuckDB |
| `HYDRADB_KEY` | HydraDB auth |
| `HYDRADB_DATABASE` | HydraDB tenant (default `default-tenant`) |
| `LLM_API_KEY` | Cognee's LLM |
| `CLAUDE_BIN` | Path to the `claude` binary if not on PATH |
| `CLAUDE_TIMEOUT_SECONDS` | How long `/api/run` waits for the workflow |
| `AGENT_SHARED_SECRET` | Protects the `/tools/*` debugging endpoints |

The agent LLM is whatever Claude Code is signed in as, so there is no separate agent
API key. `LLM_API_KEY` is only used by Cognee.

**`config.json` (saved from dashboard, gitignored):** sheet URL, tab gids, and one goal (`goal` + `target_amount`).

---

## 16. Fallbacks and cutoffs

| If this is still broken... | ...by | Do this |
|---|---|---|
| Any tool fails its first call | 0:30 | Go to the sponsor booth now |
| No hotdata key | now | `ALLOW_LOCAL_QUERY_FALLBACK=true` — same SQL, local DuckDB, labelled in every result |
| hotdata has no Sheets connector | n/a | Nothing changes; the per-run CSV reload is already the plan |
| Cognee can't write to HydraDB | 0:50 | Keep them separate; the agent already reads only HydraDB |
| No Telegram message yet | 1:15 | Drop everything else until one arrives |
| Claude Code won't authenticate | 2:05 | Run `claude` interactively and sign in. `/api/run` names this error explicitly. |
| The workflow misbehaves | 2:05 | Run `finance_cli.py context` and `send` by hand — the demo works without the model |
| Replies loop | 2:25 | Seed one preference directly through Cognee on config save to show it's honored |
| Rote | 2:45 | Drop it. Submit four working tools and state honestly what's missing. |

---

## 17. Security checklist (Snyk)

- [ ] No secrets in code. Telegram credentials and API keys stay in `.env` only.
- [ ] `config.json` holds only sheet URL and goal — no bot token.
- [ ] The bot token is never logged and never returned to the browser.
- [ ] Sheet URL: check the host, extract the ID, build the export URL ourselves (prevents SSRF).
- [ ] The SQL `last_row` value is always passed through `int()`. No user text reaches SQL.
- [ ] HydraDB reads are by `source_id`, never by concatenated query text.
- [ ] `.claude/settings.json` denies reading `.env` and allows only the three CLI
      subcommands, so the workflow cannot exfiltrate a credential even if prompted to.
- [ ] Dashboard output is HTML-escaped before being written into the run log.
- [ ] `/tools/*` endpoints require `X-Agent-Secret`.
- [ ] CORS is restricted to configured local development origins.
- [ ] Validate CSV headers and cap the file size (for example, 5 MB).
- [ ] Telegram messages are sent as plain text.
- [ ] Dependencies are pinned. Run `snyk test` (dependencies) and `snyk code test` (source) once at about 1:40 and again at 2:45.

---

## 18. Demo script (about 3 minutes)

Reset first, so run 1 is a real baseline:
`backend/.venv/bin/python backend/finance_cli.py reset`

1. Show the sheet's history. Open `/dashboard`, enter the sheet URL, goal, and target amount, and save. The Memory panel shows the goal.
2. **Run 1.** Click **Run now** — an intro message arrives on Telegram (baseline).
3. Paste **Batch A**, then **Run 2**. A nudge arrives about delivery overspend and the new Hulu charge, including its effect on the Japan goal. Log: `fresh`.
4. Reply in Telegram: *"Keep Hulu, it's shared with my roommate."*
5. Paste **Batch B**, then **Run 3**. The nudge covers delivery only, with no mention of Hulu, and the Memory panel now shows `keep: Hulu`. **This is the moment worth demoing.**
6. Paste **Batch C**, then **Run 4**. A fresh nudge flags the large Best Buy charge.
7. **Run 5** with no new rows. No message is sent, mode is `skipped`, and the run log shows it.

Record a backup screen capture of a full run before presenting.

---

## 19. Definition of done

- [ ] **hotdata** reloads the sheet and answers Q1 through Q4 on every run.
- [ ] **Cognee** processes the goal description, every Telegram reply, and every run summary.
- [ ] **HydraDB** stores goals, preferences, nudges, and runs, and is read on every run.
- [ ] **Claude Code** decides and sends the Telegram message on fresh runs.
- [ ] **Dashboard** at `/dashboard` saves sheet URL and goal, runs the agent, and shows the run log.
- [ ] **Rote** replays a captured routine on at least one later run, and the run log shows it.
- [ ] **Snyk** reports no high-severity issues.
- [ ] A reply given in Telegram changes what a later run says.
- [ ] A run with no new rows sends nothing.
- [ ] The demo script runs end to end.

---

> **Reminder:** This runbook reflects the plan as of the start of the build. If the user changes direction, follow the user and update this file.
