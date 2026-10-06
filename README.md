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

## Notes
- Back up the Evolution instance volume; losing it means re-scanning the QR.
- Keep the dedicated phone online at least every few days or WhatsApp unlinks devices.
- Stay listen-only until stable. Tell group members the number logs messages.
