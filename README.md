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

## Inventory web app

The receiver also serves a password-protected web app that turns the chosen chats' history and attachments
into a project -> phase/building -> unit inventory with full source tracing. Code: `receiver/` (`web.py`,
`history.py`, `parse_files.py`, `mapping.py`, `facts.py`, `media_review.py`, `build.py`).

**Setup (once):** on the `receiver` service add variables `APP_PASSWORD`, `EVOLUTION_URL` (the Evolution
https domain), `EVOLUTION_API_KEY` (Evolution's `AUTHENTICATION_API_KEY`), `EVOLUTION_INSTANCE` (the instance
whose chats feed the app; webhooks from any other instance are ignored) and `MARKET` (`eg` - default - or
`uae`), then Settings -> Networking -> Generate Domain and open it. **Start over** on the dashboard deletes all
app data (type `DELETE ALL`); WhatsApp and Evolution are not touched.

**Pipeline** (Dashboard, run in order; each step only processes what's new):
1. Sync chats (groups and private chats) -> tick the inventory chat(s) on **Chats**.
2. Pull history for the ticked chats (from Evolution's database).
3. Download attachments (ticked chats; WhatsApp may no longer have old media - listed on **Sources**).
4. Read files: every workbook/CSV cell and PDF page stored verbatim (`source_rows`), header + column roles
   detected per sheet (fixable on the review page).
5. Map projects (Claude): proposes parent project / phase per table using only the file and its chat context.
6. Extract facts (Claude): location, service charge, payment plan, completion, amenities, handover,
   phase/unit status - each with its source message or PDF page.
7. Review images (Claude): proposals only; nothing is shown on a project until approved.
8. Build inventory: deterministic rebuild of projects/phases/units/snapshots and the review queue.

**Rules this app enforces**
- No web data: Claude only sees the group's own messages and files.
- Source values are never changed: unit codes, listed prices and areas are stored as text exactly as in the
  cell; parsed numbers and price per sq ft are separate, labelled derived fields (with formula + inputs).
  Price per sq ft is only calculated when the price is a plain number, a currency is stated and the area
  unit (sq ft / sq m, 1 sq m = 10.7639104167 sq ft) is stated. Arabic column headers are recognised.
- Owner corrections live in `receiver/owner_rules_<market>.json` (`eg` / `uae`): market name, currency,
  price-per-area metric (sq m for Egypt, sq ft for UAE), not-a-project names, locations, one-parent groups
  (UAE: Brabus, Verdana), phase limits, image blocklist. Edit and rebuild.
- Only high-confidence in-market proposals are used before approval; everything else stays in the review queue and
  still counts in **Reconciliation** (mapped + queued + excluded = rows in sources).
- Unit status and phase status are stored separately; conflicts are flagged, not resolved silently.
- Missing project facts show "Not supplied"; conflicting statements show "Conflict" with every source.

**Feed** (`/feed`): every chat shown as a read-only WhatsApp-style channel - text with WhatsApp formatting,
images, and spreadsheets/PDFs that open in a built-in viewer (sheet tabs, row filter, exact cell values).
Attachments not downloaded yet are fetched from Evolution as you scroll to them; the feed refreshes every 15 s.

**New app (`/app`):** a React rebuild of the admin pages (Updates, Master, More > Pipeline / Inventory /
Review / Sources; Chats and Insights coming next), on the JSON API in `receiver/api.py` (`/api/*`, same login).
Source and dev notes: `receiver/frontend/README.md`. The Docker image builds it; the classic pages keep working.

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
