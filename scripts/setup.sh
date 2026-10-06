#!/usr/bin/env bash
# Run from your VM. Usage: ./scripts/setup.sh create | webhook | qr | status | groups
set -euo pipefail
source .env
API="${SERVER_URL%/}"
H=(-H "apikey: $AUTHENTICATION_API_KEY" -H "Content-Type: application/json")

case "${1:-}" in
  create)
    curl -s -X POST "$API/instance/create" "${H[@]}" \
      -d "{\"instanceName\":\"$INSTANCE_NAME\",\"integration\":\"WHATSAPP-BAILEYS\",\"qrcode\":true}"; echo ;;
  webhook)
    curl -s -X POST "$API/webhook/set/$INSTANCE_NAME" "${H[@]}" -d "{
      \"webhook\": {
        \"enabled\": true,
        \"url\": \"$RECEIVER_INTERNAL_URL/webhook/$WEBHOOK_TOKEN\",
        \"webhookByEvents\": false,
        \"webhookBase64\": false,
        \"events\": [\"MESSAGES_UPSERT\", \"GROUPS_UPSERT\"]
      }}"; echo ;;
  qr)
    curl -s "$API/instance/connect/$INSTANCE_NAME" "${H[@]}" | python3 -c "
import sys, json, base64
d = json.load(sys.stdin); b = (d.get('base64') or '').split(',')[-1]
if b: open('qr.png', 'wb').write(base64.b64decode(b)); print('Saved qr.png - scan from WhatsApp > Linked devices')
else: print(d)" ;;
  status)
    curl -s "$API/instance/connectionState/$INSTANCE_NAME" "${H[@]}"; echo ;;
  groups)
    curl -s "$API/group/fetchAllGroups/$INSTANCE_NAME?getParticipants=false" "${H[@]}" \
      | python3 -c "import sys,json; [print(g['id'], '|', g.get('subject')) for g in json.load(sys.stdin)]" ;;
  *) echo "usage: $0 create|webhook|qr|status|groups"; exit 1 ;;
esac
