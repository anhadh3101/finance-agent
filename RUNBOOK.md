# Runbook: Personal Finance Memory Agent

> **This plan can change.** It is a starting point, not a contract. If the user's instructions differ from anything in this runbook, follow the user and update this file to match.

**Event:** Data and AI Hackathon, Sep 11, 2026, AWS Builder Loft
**Build time:** 3 hours
**Goal:** Meet the basic requirements: all five sponsor tools doing real, repeated work, plus a clean Snyk scan. Finish on time. Winning is not the goal.

---

## 1. What we're building

A spending coach that remembers you. The user opens a web page, links a public Google Sheet of transactions, enters a savings goal, and connects a Telegram bot. On each run (a button click or a timer), the agent does four things:

1. Checks the sheet for new transactions.
2. Compares spending against budgets and the goal.
3. Remembers what the user has told it before.
4. Sends a Telegram message: a nudge if something needs attention, a short "on track" note if not, or nothing at all if no new data came in.

The user replies in Telegram, for example "keep Hulu, it's shared with my roommate", and the agent respects that on later runs. Once a run succeeds, its workflow is captured and replayed on later runs, so those runs cost less.

---

## 2. Decisions already made

| Area | Decision |
|---|---|
| Frontend | Next.js, one page |
| Backend | FastAPI (Python) |
| Login | None. The form fields act as the configuration. Memory is keyed by Telegram `chat_id`. |
| Transaction source | Public Google Sheet with a `transactions` tab and a `budgets` tab |
| Notifications | Telegram bot. The user provides the bot token and chat ID. |
| Budgets | Live only in the sheet's `budgets` tab. The web form holds goals, not category limits. |
| Production | Not deploying. Local demo only. |
| Scheduling | "Run now" button, plus an optional auto-run every 2 minutes |
| Rote success rule | Default: the run completed and the Telegram message was delivered. Stretch goal: the nudged category's spending dropped afterward. |

---

## 3. Verify first (T+0:00 to T+0:30)

These are the open questions that can sink the build. Test each one in 5 minutes or less. Write the answers down here, because they decide which fallbacks from section 16 apply.

- [ ] **Python version.** Check which Python versions Cognee supports before creating the venv.
- [ ] **hotdata:** Can we load a CSV into a table, query it, then reload and replace it? What is the exact CLI or API call? Which SQL dialect (does `date_trunc` work)? Is there a Python client, or do we shell out to the CLI? Does it keep data between calls, or is it ephemeral?
- [ ] **hotdata and Sheets:** Is there a native Sheets connector? If so, does a newly added row appear without re-importing? *We default to a CSV reload on every run either way.*
- [ ] **Cognee:** Do `add` and `cognify` run with our LLM key? Is it `add`/`cognify`/`search` or `remember`/`recall` in the installed version? Can HydraDB be its graph store? If not, how do we read the extracted nodes and edges out of Cognee? Does `cognify` accept a custom graph model?
- [ ] **HydraDB:** Connect from Python, then do one `MERGE` and one `MATCH` round trip. Are parameterized queries supported?
- [ ] **RocketRide:** Does it run in the cloud (staging.rocketride.ai) or locally? Can it call HTTP endpoints as tools? *If it runs in the cloud, it cannot reach `localhost`. See section 11.* Does it report token counts? Are the coupon credits applied?
- [ ] **Rote:** How does it capture a run: by watching API calls, or by wrapping commands? How do we trigger a replay? Can it reach our endpoints and the Telegram API?
- [ ] **Telegram:** Create the bot with BotFather, message it once, confirm `getUpdates` shows the chat ID, and confirm `sendMessage` works.
- [ ] **Snyk:** Run `snyk auth`, then confirm `snyk test` and `snyk code test` work on the empty repo.

**Rule:** If a tool is still broken at T+0:30, go to that sponsor's booth right away. Don't debug alone.

---

## 4. Timeline

This assumes one person. With a second person, they build the web page (section 13) from T+0:30 in parallel, and the solo page slot goes back to the backend.

| Time | Work | Checkpoint |
|---|---|---|
| 0:00 to 0:30 | Verify list (section 3). In parallel: build the seed sheet (section 6), create the Telegram bot, scaffold the repo. | Every tool answers one call |
| 0:30 to 1:15 | Core run as a plain script: sheet to hotdata to queries; goals to Cognee to HydraDB; message to Telegram | **A real Telegram message arrives** |
| 1:15 to 1:40 | Minimal Next.js page: config form, find chat ID, Run button, output panel | Page triggers a run |
| 1:40 to 2:05 | Move the run into RocketRide (tool endpoints) | RocketRide sends the message |
| 2:05 to 2:25 | Telegram replies to Cognee to HydraDB, run summaries, `last_row` tracking | "Keep Hulu" test passes |
| 2:25 to 2:45 | Rote capture and replay, run log | Run log shows a "replay" row |
| 2:45 to 3:00 | Snyk scan and fixes, one full rehearsal (section 18) | Submitted |

**Hard rule:** Stop adding features at 2:45, whatever state things are in.

---

## 5. Architecture

```
            Next.js page (localhost:3000)
                        |
                        v
               FastAPI (localhost:8000)
   /api/config  /api/find-chat-id  /api/run  /api/state
                        |
                /api/run decides:
      captured routine covers this situation?
            yes |                 | no
                v                 v
          Rote replay       RocketRide agent
                \                /
                 \  both call the same tool endpoints
                  v             v
     /tools/refresh-data    -> Google Sheet CSV -> hotdata (tables)
     /tools/spend-analysis  -> hotdata SQL
     /tools/memory          -> HydraDB Cypher
     /tools/send-telegram   -> Telegram Bot API
     /tools/record-run      -> HydraDB writes + Cognee (run summary)

Before each run: Telegram getUpdates -> replies -> Cognee -> HydraDB
```

### What each tool reads and produces

| Tool | Reads | Produces |
|---|---|---|
| **Google Sheet** | Transactions the user adds | CSV downloaded on every run |
| **hotdata** | `transactions` and `budgets` tables loaded from the CSV | Spend vs. budget, new rows, large charges, new subscriptions |
| **Cognee** | Words only: goal notes, Telegram replies, run summaries | Entities and relationships (preferences, patterns, goal links) |
| **HydraDB** | Cognee's graph, structured goal fields, nudges, runs | Answers to Cypher queries: goals, preferences, repeated overspending, nudges that worked, `last_row` |
| **RocketRide** | hotdata results and HydraDB memory | Decision and message text, the Telegram send, write-backs |
| **Rote** | A successful RocketRide run | A replayable routine used on later runs |
| **Snyk** | The repo | A vulnerability report, which we fix before submitting |

**Boundary rule:** Raw transaction rows never go to Cognee, and text never goes to hotdata. The only link between them is the run summary, which our code writes from hotdata's results.

---

## 6. Data contracts

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

The form asks for the sheet URL, the `transactions` tab gid (default `0`), and the `budgets` tab gid.

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

## 8. HydraDB schema

Memory is keyed by `chat_id`, which gives each person separate memory without a login.

```
(:User {chat_id})
(:User)-[:HAS_GOAL]->(:Goal {name, target, saved, deadline})        <- structured form fields, written directly
(:User)-[:IGNORES]->(:Merchant {name})                               <- from Cognee
(:User)-[:PROTECTS]->(:Category {name})                              <- from Cognee
(:User)-[:KEEPS]->(:Subscription {name})                             <- from Cognee
(:Category)-[:OVER_BUDGET_IN]->(:Month {key, spent, limit})          <- from run summaries
(:Category)-[:THREATENS]->(:Goal)                                    <- from Cognee
(:AgentRun {id, time, last_row, tg_offset, mode, new_rows,
            flags, llm_calls, tokens, latency_ms, sent})
(:AgentRun)-[:SENT]->(:Nudge {text, category, flag_types, sent_at})
(:Nudge)-[:GOT_REPLY]->(:Reply {text, at})
(:Nudge)-[:RESULTED_IN]->(:Outcome {result})                         <- stretch
```

### Queries used on every run (`/tools/memory`)

```cypher
MATCH (u:User {chat_id: $chat_id})-[:HAS_GOAL]->(g:Goal) RETURN g;

MATCH (u:User {chat_id: $chat_id})-[r:IGNORES|PROTECTS|KEEPS]->(x)
RETURN type(r) AS kind, x.name AS target;

MATCH (c:Category)-[:OVER_BUDGET_IN]->(m:Month) RETURN c.name, count(m) AS times;

MATCH (n:Nudge)-[:RESULTED_IN]->(:Outcome {result: 'improved'})
RETURN n.category, n.text ORDER BY n.sent_at DESC LIMIT 3;

MATCH (r:AgentRun) RETURN r ORDER BY r.time DESC LIMIT 1;
```

**Always use query parameters** (`$chat_id`), never string formatting. Snyk flags query injection.

---

## 9. Cognee

### What goes in

| Input | When | Dataset |
|---|---|---|
| Goal notes (the free-text box) | On `/api/config` save | `user_{chat_id}` |
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
```

### The schema problem

Cognee's LLM chooses its own entity and relationship names, so its output won't reliably use `IGNORES`, `PROTECTS`, or `KEEPS`. Our Cypher queries depend on those names. Solve it in this order:

1. **Preferred:** If `cognify` accepts a custom graph model, define `Preference {kind: ignore|protect|keep, target, reason}` and `Goal` so the output uses our schema.
2. **Fallback:** After `cognify`, read Cognee's nodes and edges and normalize them while writing to HydraDB with `MERGE`. For example, a relationship name containing "ignore" or "skip" becomes `IGNORES`; "keep" becomes `KEEPS`; "protect", "never cut", or "non-negotiable" becomes `PROTECTS`.
3. **Last resort:** Store each extracted fact as `(:User)-[:REMEMBERS]->(:Memory {text, kind})` and pass the text to RocketRide, letting its LLM interpret it.

If Cognee can't use HydraDB as its store, Cognee keeps its own copy of the graph. That's fine, as long as **RocketRide reads only from HydraDB**.

`cognify` calls an LLM and is slow. Run it after the Telegram message has been sent so the user doesn't wait on it.

---

## 10. The agent run, step by step

`POST /api/run` does the following, with a lock so two runs can't overlap:

0. **Fetch replies.** Call Telegram `getUpdates` with `offset = tg_offset`. For each new text message from this `chat_id`, send it to Cognee, attach a `Reply` to the last `Nudge`, and advance `tg_offset`.
1. **Refresh data.** Call `/tools/refresh-data`.
2. **Check for new rows.** Read `last_row` from the latest `AgentRun`, or 0 if none exists.
   - **First run ever (baseline):** Skip Q2 through Q4. Send one intro message summarizing month-to-date spending and the goal. Record `last_row = max row`, and stop.
   - **No new rows:** Record a run with mode `skipped` and send **nothing**.
3. **Analyze.** Call `/tools/spend-analysis`, which runs Q1 through Q4 and returns candidate flags:
   - `overspend`: a category that had new rows this run, with month-to-date spend above 1.2 × expected pace or above its monthly limit
   - `large_charge`: rows from Q3
   - `new_subscription`: rows from Q4
4. **Load memory.** Call `/tools/memory`.
5. **Decide and write** (RocketRide or a Rote replay):
   - Drop flags covered by preferences: ignored merchants, protected categories, subscriptions the user keeps.
   - **Flags remain:** Write a nudge of 4 sentences or fewer. Mention the goal impact, and reuse the suggestion from a nudge that worked before, if one exists.
   - **No flags remain:** Send a short "on track" note with month-to-date spend vs. pace.
   - **Goal impact:** `needed_per_month = (target - saved) / months_left`. Show overspend as a share of that amount.
6. **Send.** Call `/tools/send-telegram` with plain text only, no `parse_mode`, so the text needs no escaping.
7. **Record.** Call `/tools/record-run`: write `AgentRun` and `Nudge` to HydraDB, update `last_row`, then send the run summary to Cognee.

---

## 11. RocketRide

**Role:** RocketRide runs steps 3 through 7 by calling the `/tools/*` endpoints and making the decision in step 5.

**Instructions for its agent (outline):**
- Call `spend-analysis`, then `memory`.
- Remove any flag that matches a preference.
- Decide whether to send a nudge or an "on track" note.
- Write the message using the rules in step 5.
- Call `send-telegram`, then `record-run`, including `flag_types` and the message.

**Reachability issue:** If RocketRide runs in the cloud, it cannot call `localhost:8000`. The options, in order:

1. Expose FastAPI with a tunnel such as `cloudflared tunnel --url http://localhost:8000` or ngrok, and give RocketRide that URL. **Protect every `/tools/*` endpoint with an `X-Agent-Secret` header**, because the tunnel makes them public.
2. If RocketRide has a local runtime or SDK, run it on the laptop and skip the tunnel.
3. Fallback: FastAPI runs steps 3, 4, 6, and 7 in code, and makes one RocketRide call for the decision and message text in step 5. This is weaker, but RocketRide still does real work.

The bot token stays in FastAPI. RocketRide never sees it; it calls `/tools/send-telegram`.

---

## 12. Rote

**Role:** Capture the first successful nudge run and replay it on later runs.

**Routing in `/api/run`:**
- After step 3, compute `flag_types` for this run, for example `{overspend}`.
- **A captured routine exists and `flag_types` is a subset of the types it handled:** replay it with Rote. The LLM only writes the message wording. Log mode `replay`.
- **Otherwise:** run fresh with RocketRide. If the run succeeds, capture it with Rote (or re-capture it with the wider set of types). Log mode `fresh`.

**What counts as success:** the run completed and Telegram returned `ok: true`. This is the default for the 3-hour build.
**Stretch goal:** mark a nudge `improved` when the category's spending in the 7 days after the nudge is lower than in the 7 days before. Then capture only improved runs.

**Expected run log for the demo** (these numbers illustrate the shape, not actual measurements):

| Run | Situation | Mode |
|---|---|---|
| 1 | Baseline | fresh |
| 2 | Delivery overspend and Hulu | fresh, captured |
| 3 | Delivery overspend (Hulu is kept) | **replay** |
| 4 | Large Best Buy charge | fresh (a new situation) |
| 5 | No new rows | skipped |

LLM call and token counts come from RocketRide's response metadata if it provides them. Otherwise, count them in our own code.

---

## 13. FastAPI endpoints

**User-facing** (CORS allows only `http://localhost:3000`):

| Endpoint | Does |
|---|---|
| `POST /api/config` | Validates and saves the sheet URL, gids, bot token, and chat ID to `config.json`. Writes the structured goal to HydraDB. Sends goal notes to Cognee. |
| `POST /api/find-chat-id` | Takes the token, calls `getUpdates`, returns the latest chat ID, and sets `tg_offset` past that message so it isn't treated as a reply later. |
| `POST /api/run` | Runs one cycle (section 10) and returns the result, message, and mode. |
| `POST /api/autorun` | Turns the 2-minute background loop on or off. |
| `GET /api/state` | Returns memory items and the run log from HydraDB, plus the last message. |

**Agent tools** (require the `X-Agent-Secret` header): `/tools/refresh-data`, `/tools/spend-analysis`, `/tools/memory`, `/tools/send-telegram`, `/tools/record-run`.

Telegram notes: if the bot ever had a webhook set, `getUpdates` returns a 409 error, so call `deleteWebhook` first. A bot cannot message a user until that user has messaged the bot.

---

## 14. Web page (Next.js, one page)

1. **Setup:** Sheet URL, transactions gid, budgets gid, bot token, chat ID (with a "Find my chat ID" button), goal name, target, amount saved so far, deadline, and a notes box ("Anything I should know?"). Save button.
2. **Run:** "Run now" button, auto-run toggle, and the last message sent.
3. **Memory:** Goals and preferences from `GET /api/state`.
4. **Run log:** time, new rows, flags, mode, LLM calls, tokens.

After saving, never show the token again. Display it masked, like `••••1234`. Use plain form elements and no styling work.

**Fallback if the page falls behind:** FastAPI's built-in Swagger UI at `localhost:8000/docs` can run every endpoint. Use it for the demo.

---

## 15. Repo layout and config

```
finance-agent/
  backend/
    main.py            # FastAPI app, /api/* routes, autorun loop, run lock
    tools.py           # /tools/* routes
    sheet.py           # URL validation, CSV download, cleaning
    hotdata_client.py  # load tables and run queries
    hydra.py           # Cypher helpers (parameterized)
    memory.py          # Cognee add/cognify and the Cognee-to-HydraDB copy
    telegram.py        # sendMessage, getUpdates, find chat ID
    agent.py           # RocketRide trigger and Rote replay routing
    requirements.txt   # pinned versions
  web/                 # Next.js page
  .env.example
  .gitignore           # .env, config.json, .venv, node_modules, service keys
  runbook.md
```

**`.env` (server secrets, never committed):** `HOTDATA_API_KEY`, `HYDRADB_URL`, `HYDRADB_CREDENTIALS`, `LLM_API_KEY` (for Cognee), `ROCKETRIDE_API_KEY`, `AGENT_SHARED_SECRET`, `PUBLIC_BASE_URL` (the tunnel URL). Use whatever variable names each SDK actually expects.

**`config.json` (saved from the web form, gitignored):** sheet ID, gids, bot token, chat ID.

---

## 16. Fallbacks and cutoffs

| If this is still broken... | ...by | Do this |
|---|---|---|
| Any tool fails its first call | 0:30 | Go to the sponsor booth now |
| hotdata has no Sheets connector | n/a | Nothing changes; the per-run CSV reload is already the plan |
| Cognee can't write to HydraDB | 0:50 | Normalize and copy with `MERGE` (section 9, option 2) |
| No Telegram message yet | 1:15 | Drop everything else until one arrives |
| Next.js page | 1:40 | Use Swagger at `/docs` and come back to the page only if time remains |
| RocketRide can't reach our tools | 2:05 | Run it locally, or use option 3 in section 11 |
| Replies loop | 2:25 | Seed one preference directly through Cognee on config save to show it's honored |
| Rote | 2:45 | Drop it. Submit four working tools and state honestly what's missing. |

---

## 17. Security checklist (Snyk)

- [ ] No secrets in code. Keep them in `.env` and `config.json`, both gitignored.
- [ ] The bot token is never logged, never returned to the browser, and never sent to RocketRide.
- [ ] Sheet URL: check the host, extract the ID, build the export URL ourselves (prevents SSRF).
- [ ] Cypher uses parameters only. The SQL `last_row` value is always passed through `int()`.
- [ ] `/tools/*` endpoints require `X-Agent-Secret`.
- [ ] CORS is restricted to `http://localhost:3000`.
- [ ] Validate CSV headers and cap the file size (for example, 5 MB).
- [ ] Telegram messages are sent as plain text.
- [ ] Dependencies are pinned. Run `snyk test` (dependencies) and `snyk code test` (source) once at about 1:40 and again at 2:45.

---

## 18. Demo script (about 3 minutes)

1. Show the sheet's history. Fill in the form and save. The memory panel shows the goal.
2. **Run 1.** An intro message arrives on Telegram (baseline).
3. Paste **Batch A**, then **Run 2**. A nudge arrives about delivery overspend and the new Hulu charge, including its effect on the Japan goal. Log: `fresh`, captured.
4. Reply in Telegram: *"Keep Hulu, it's shared with my roommate."*
5. Paste **Batch B**, then **Run 3**. The nudge covers delivery only, with no mention of Hulu. Log: `replay`, with fewer LLM calls.
6. Paste **Batch C**, then **Run 4**. A fresh nudge flags the large Best Buy charge.
7. **Run 5** with no new rows. No message is sent. Show the run log and the memory panel.

Record a backup screen capture of a full run before presenting.

---

## 19. Definition of done

- [ ] **hotdata** reloads the sheet and answers Q1 through Q4 on every run.
- [ ] **Cognee** processes goal notes, every Telegram reply, and every run summary.
- [ ] **HydraDB** stores goals, preferences, nudges, and runs, and is queried on every run.
- [ ] **RocketRide** makes the decision and sends the Telegram message on fresh runs.
- [ ] **Rote** replays a captured routine on at least one later run, and the run log shows it.
- [ ] **Snyk** reports no high-severity issues.
- [ ] A reply given in Telegram changes what a later run says.
- [ ] A run with no new rows sends nothing.
- [ ] The demo script runs end to end.

---

> **Reminder:** This runbook reflects the plan as of the start of the build. If the user changes direction, follow the user and update this file.
