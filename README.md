# Byit WhatsApp Bridge

Listen-only: a dedicated WhatsApp number sits in the ops groups, Evolution API forwards
group messages to the receiver, the receiver stores them, and `digest.py` asks Claude
for a summary. Nothing is sent to WhatsApp.

WhatsApp groups -> Evolution API (Railway, public) -> receiver (Railway, private) -> Claude

## Railway checklist

1. **Evolution API**: New Project -> Deploy template "Evolution API" (includes Postgres + Redis).
   Set `AUTHENTICATION_API_KEY` (long random). Settings -> Networking -> Generate Domain,
   then set `SERVER_URL` to that https domain. Redeploy.
2. **Receiver**: + New -> GitHub Repo -> this repo. Root directory: `receiver`.
   Service name must be exactly `receiver`. No public domain.
3. **Receiver variables**: `WEBHOOK_TOKEN`, `ANTHROPIC_API_KEY`, `CLAUDE_MODEL=claude-sonnet-5-5`,
   `DB_PATH=/data/messages.db`. Attach a volume mounted at `/data`.
4. **On your VM**: `cp .env.example .env`, fill the top block, then:
   `./scripts/setup.sh create` -> `./scripts/setup.sh webhook` -> `./scripts/setup.sh qr`
5. **Phone**: open `qr.png`, WhatsApp -> Linked devices -> Link a device -> scan.
   `./scripts/setup.sh status` should show `"state":"open"`.
6. **Test**: send a message in a group; receiver logs should show `POST /webhook/... 200`.
   Digest: `railway ssh --service receiver python digest.py`

## Web app

The receiver serves a password-protected web app at `/app` (the root URL opens it). Navigation, in order:
**Chats**, **Insights**, **Sheet** (the master as a Google-Sheets-like grid with approvals and open suggestions),
**Updates** (the review queue), then **Master** and More (**Pipeline**, **Sources**, **Brain**). Code: `receiver/`
(API in `api.py`, React app in `receiver/frontend/`).

**Setup (once):** on the `receiver` service add variables `APP_PASSWORD`, `EVOLUTION_URL` (the Evolution
https domain), `EVOLUTION_API_KEY` (Evolution's `AUTHENTICATION_API_KEY`), `EVOLUTION_INSTANCE` (the instance
whose chats feed the app; webhooks from any other instance are ignored) and `MARKET` (`eg`, the default), then
Settings -> Networking -> Generate Domain and open it. **Start over** on Pipeline deletes all app data (type
`DELETE ALL`); WhatsApp and Evolution are not touched.

**Pipeline:** **Sync all chats** lists chats, pulls history, reads text first, then downloads and reads attachments
(every workbook/CSV cell and PDF page stored verbatim in `source_rows`) and proposes sheet updates. On their own:
Propose sheet updates, Brain review, Eval, Retrain, Re-read skipped messages, Rule suggestions.

The old project/unit inventory (map projects, extract facts, review images, build inventory, and the Inventory,
Review and Reconciliation screens) was removed; its tables are left untouched in existing databases.

**Feed** (`/feed`): every chat shown as a read-only WhatsApp-style channel, with a built-in viewer for
spreadsheets and PDFs.

**Tests:** `cd receiver && pip install -r requirements.txt pytest && python -m pytest tests`

## Notes
- Back up the Evolution instance volume; losing it means re-scanning the QR.
- Keep the dedicated phone online at least every few days or WhatsApp unlinks devices.
- Stay listen-only until stable. Tell group members the number logs messages.

## Master workbook updates (App Updates sheet)

The team's **App Updates** workbook is the target output. Upload it on **Master**; the app reads New Cairo,
Sokhna&new zayed, New Capital (price grids), Sheet 2 (commission) and Separated update. Other sheets are kept
untouched in the export.

1. **Master** -> upload the .xlsx. Rows whose location doesn't belong to their sheet (October / North Coast
   only on Sokhna, unknown codes such as `L`, `de`, `oman`) are listed under "Rows to place": Move / Keep / Drop.
2. Dashboard step **6b. Propose sheet updates (Claude)** reads new messages, workbook rows and PDF pages.
   Claude only reads values in the sheet's notation; matching, sheet routing and the old-vs-new comparison
   are done in code (`master.py`). Only changed values become proposals; the newest quote per cell wins.
3. **Updates** -> approve / reject. Approving writes today's date on the row (on Sheet 2 this replaces
   Hide/Hold). A near name match ("Jirian" -> "Jiran") is saved as an alias when approved.
4. **Master** -> download the **Team file** (your workbook with approved values in the same cells, payment
   plans normalised to `10% - 8Y`, new projects appended) or the **SQL-ready file** (typed split columns +
   a `units` sheet). Upload the team file back as the new master once it's been shared.

Ingestion may propose: location, Ready To Move, unit-type cells, payment plan, commission (number + `net`),
Sheet 2 feedback. It never touches sales name / contact / number, voice note or ID.
Location codes and sheet routing live in `owner_rules_eg.json` (`master_location_codes`,
`master_location_sheets`). Code: `cell_parse.py`, `master_schema.py`, `master.py`, `proposals.py`,
`master_export.py`. Test against the real file: `MASTER_XLSX=/path/file.xlsx pytest tests/test_master.py`.

## Reading order (step 6b and Sync now)

Every WhatsApp group is read (private chats only when ticked; a group can be unticked to leave it out).
Sources are read in this order, and each later one only fills what is still missing:

1. **Text messages** from all those chats (`CLAUDE_MODEL`, default Sonnet 5.5).
2. **Spreadsheets**: rows read cell by cell, then by `CLAUDE_MODEL`.
3. **PDFs**: sent whole to Claude (`CLAUDE_FILE_MODEL`, default Opus 5.5), which reads page images plus text, so
   scanned brochures and tables work. Up to 20 pages per request.
4. **Images**: read with Claude's vision (`CLAUDE_FILE_MODEL`); price-list screenshots and brochure photos.

"Missing" means: no earlier source in this order gave a value for that project + column in the last 14 days
(an empty cell counts). A file never overrides a fresh text value; a newer file can refresh a value older than
14 days. Chat context (caption and nearby messages) is sent with each file only to tell which project it is about.

Step 6b runs through the Message Batches API at half price: one batch per stage, collected by a background
poller every `INGEST_POLL_SECONDS` (default 60), usually within an hour per stage. **Sync now** on one chat runs
the same four stages live. Settings: `CLAUDE_EFFORT` (default `medium`), `INGEST_MODE=live` to run 6b without the
batch queue, `CLAUDE_FALLBACKS=0` to turn off the server-side refusal fallback.

## Brain (second look before a person)

After every sync (Sync now, Sync all chats, the batched run) the brain (`brain.py`, `CLAUDE_BRAIN_MODEL`, default
Opus 5.5) reviews each open suggestion against the sheet rows it could mean: similar names, the same developer's
rows, and rows this chat has updated before. It:

- moves a false "new project" onto the existing row (the chat's spelling becomes an alias once approved);
- **auto-approves** only when all hold: existing row, high confidence, value in sheet format, unit price within
  ±20% (`BRAIN_PRICE_BAND`), no Hold/Hide, and the column isn't commission, feedback or location;
- **auto-rejects** clear noise (resale/client units, misread values) and duplicates the row already has;
- leaves the rest on Updates with its reason. New rows always need a person.

Every brain decision shows "Approved/Rejected by brain" with its reason and a **Reopen** link. More > Pipeline >
6c (or "Review Backlog Now" on Updates) reviews the existing backlog.

## Sheet view (check the brain against your own master)

**Sheet** (desktop rail; on phones under More) shows each master sheet like Google Sheets: column letters, real row
numbers, frozen Developer/Project columns, a formula bar, tabs at the bottom, arrow-key navigation (Ctrl+Arrow,
Home/End, PageUp/Down, Enter for details, Ctrl+F to search, Ctrl+C to copy).
"After approvals" shows the values the next export will write; "As uploaded" shows the master you uploaded.
Approved cells are tinted (darker when the brain approved them), open suggestions have a dashed outline with the
suggested value under the current one, and suggested new rows appear at the bottom in grey. Click a highlighted
cell for the master value, the change, the source quote and the brain's reason, and approve, reject or reopen it.
API: `GET /api/sheet?sheet=&view=all|changed|pending|approved&q=`.

## Developer profiles, rounding and Data Missing

- **Developer profile** (chat header chip): which developer the chat belongs to (auto from "<Developer> & Byit App"),
  whether they send full inventory or starting prices only, unit types sent only on request, notes for the brain.
  Suggestions from the chat match that developer's rows first; a developer name that is also a place (Soma Bay)
  is never read as a project.
- **Rounding**: suggested prices round up to the next 50,000 from 1,000,000 EGP, otherwise to the next 1,000.
- **Insights > Data Missing**: developers whose group is active but whose price rows had no change for 14 days,
  with the sales rep from Sheet 2 (WhatsApp link) and a ready-to-send message.

## Group assignment by the ops team (Phase 0)

Above each group chat: **Developer** (a dropdown of the master's developers; "Suggested" when it was only read from the
chat name; "Not on the sheet yet" for a new one) and **Projects** (that developer's projects on the master). The chat
list shows each group's developer or **Unassigned**, has an Unassigned filter and **Assign Groups** for bulk.
Every ingestion from the group uses it: extraction prompts name the assigned projects, matching tries them first,
and a match outside them is only a near match (warning "Outside this group's assigned projects") that the brain
never auto-approves. Assignments are keyed by normalized developer + project, re-checked on every master upload
(`profiles.relink_projects`; missing ones are flagged, not dropped), and every change is logged in
`assignment_log` as a team label. Groups the team hasn't assigned behave as before.

## Eval harness (Phase 1)

`receiver/eval/`: `build_gold.py` freezes every decision a **person** made (approved or rejected) into a gold set under
`/data/eval/gold-<date>/`, split by source kind. `run_eval.py` replays matching on it and measures noise, accepted
values, auto-approve precision and API cost (from `llm_usage`). It's free by default; `--llm N --max-usd X` is an opt-in
paid replay. Run it from More > Pipeline > "Eval: brain report (no LLM)". See BASELINE.md.

Labels survive master re-uploads: proposals and brain reviews carry `row_key` (normalized developer | project | sheet)
and are re-linked to the new rows on import (`label_row_id`; an old approval is never replayed onto the new master).
A rejection can carry a reason (Updates asks "Why?"). `BRAIN_AUTO_APPROVE` / `BRAIN_AUTO_REJECT` (default `1`, today's
behaviour) can turn the brain's auto-decisions off. `receiver/normalize.py`: Arabic/Franco normalization and a
transliteration key that lets 'ماونتن فيو' and 'Mountain View' meet.

## Learning brain (Phases 2–7)

Everything below is **off by default**: with no new variables set, matching, extraction and the brain behave exactly
as before. More > **Brain** shows model versions, eval per source kind, what reaches an LLM, cost per day, the flags
and rule suggestions. Models live under `/data/models/` (versioned, with a `-current` pointer).

| Phase | What | Turn on with |
|---|---|---|
| 2 | Unified matcher (`matcher.py`): rapidfuzz + char n-gram TF-IDF on names and transliteration keys, developer-scoped aliases (`dev_aliases`), team assignment, chat history; trained scorer (`train_matcher.py`) | `MATCHER=shadow` (logs disagreements, legacy still decides) → `MATCHER=v2`. `MATCH_AUTO=<p>` treats matches above p as certain (unset = never) |
| 2 | Brain places suggestions with the matcher; only the rest go to Haiku with the top 3 rows | `BRAIN_MATCHER=v2` (+ `MATCH_AUTO` for LLM-free placement) |
| 3 | Learning memory (`learned_examples`, every person decision and Reopen); past decisions in the user turn of extraction and brain requests | `FEWSHOT=1` (`FEWSHOT_K`, default 6) |
| 3 | Weekly rule suggestions from rejections → `/data/review/rule-suggestions-<date>.json` | always on, never applied |
| 4 | Noise filter for text messages (`noise.py`); skips are logged and re-queueable (Pipeline: Re-read skipped) | `NOISE_SKIP=<p>` only after the eval shows false skips < 1% |
| 5 | Egyptian notation in `cell_parse` (Arabic digits, مليون/ألف, م², مقدم/سنين, غرف, صافي) | always on (raw text kept verbatim) |
| 5 | Spreadsheet templates: "Confirm Columns" on a sheet-row source; same layout from the same developer read in code | `TEMPLATES=1` |
| 5 | Extraction cache for re-reads and forwarded messages (same developer only) | `EXTRACTION_CACHE=1` |
| 6 | Price guards (`guards.py`, EGP/sq m median/MAD) + matcher probability instead of the model's "high" for auto-approve | `GUARDS_V2=1` (needs `MATCH_AUTO`) |
| 7 | Retrain loop (`retrain.py`): nightly when 25+ new labels, after a master re-upload, or Pipeline: Retrain; promotes only if no worse | always on; `RETRAIN_MIN_LABELS` |

Never auto-approved, whatever the flags: commission, feedback, location, Hold/Hide, new rows, anything outside a
group's assigned projects. Models never write to the sheet; every change goes through `master.decide_detail`.
Suggested order to enable: run Pipeline > Retrain and Eval; `MATCHER=shadow` for a few days; compare legacy vs
unified on More > Brain; then `MATCHER=v2`, `BRAIN_MATCHER=v2`, `EXTRACTION_CACHE=1`, `FEWSHOT=1`; set `MATCH_AUTO`
from the trainer's suggestion; `GUARDS_V2=1`; `NOISE_SKIP` last.
