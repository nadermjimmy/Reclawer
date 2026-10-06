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

The receiver also serves a password-protected web app that turns the UAE group's history and attachments
into a project -> phase/building -> unit inventory with full source tracing. Code: `receiver/` (`web.py`,
`history.py`, `parse_files.py`, `mapping.py`, `facts.py`, `media_review.py`, `build.py`).

**Setup (once):** on the `receiver` service add variables `APP_PASSWORD`, `EVOLUTION_URL` (the Evolution
https domain), `EVOLUTION_API_KEY` (Evolution's `AUTHENTICATION_API_KEY`), `EVOLUTION_INSTANCE=byit-ops`,
then Settings -> Networking -> Generate Domain and open it.

**Pipeline** (Dashboard, run in order; each step only processes what's new):
1. Sync groups -> tick the UAE inventory group(s) on **Groups**.
2. Pull history (all groups, from Evolution's database).
3. Download attachments (in-scope groups; WhatsApp may no longer have old media - listed on **Sources**).
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
  unit (sq ft / sq m, 1 sq m = 10.7639104167 sq ft) is stated.
- Owner corrections live in `receiver/owner_rules.json` (not-a-project names, locations, one-parent groups
  such as Brabus and Verdana, phase limits, image blocklist). Edit it and rebuild.
- Only high-confidence UAE proposals are used before approval; everything else stays in the review queue and
  still counts in **Reconciliation** (mapped + queued + excluded = rows in sources).
- Unit status and phase status are stored separately; conflicts are flagged, not resolved silently.
- Missing project facts show "Not supplied"; conflicting statements show "Conflict" with every source.

**Tests:** `cd receiver && pip install -r requirements.txt pytest && python -m pytest tests`

## Notes
- Back up the Evolution instance volume; losing it means re-scanning the QR.
- Keep the dedicated phone online at least every few days or WhatsApp unlinks devices.
- Stay listen-only until stable. Tell group members the number logs messages.
