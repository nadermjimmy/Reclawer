"""Read-only calls to the Evolution API. Nothing here sends anything to WhatsApp."""
import os
import httpx

URL = os.getenv("EVOLUTION_URL", "").rstrip("/")
KEY = os.getenv("EVOLUTION_API_KEY", "")
INSTANCE = os.getenv("EVOLUTION_INSTANCE", "byit-ops")


def _client():
    if not URL or not KEY:
        raise RuntimeError("EVOLUTION_URL and EVOLUTION_API_KEY must be set on the receiver service")
    return httpx.Client(base_url=URL, headers={"apikey": KEY}, timeout=120)


def fetch_groups():
    with _client() as h:
        r = h.get(f"/group/fetchAllGroups/{INSTANCE}", params={"getParticipants": "false"})
        r.raise_for_status()
        return r.json()


def find_messages(jid, page, page_size=100):
    """One page of a chat's stored history. Returns (records, total_pages)."""
    with _client() as h:
        r = h.post(f"/chat/findMessages/{INSTANCE}",
                   json={"where": {"key": {"remoteJid": jid}}, "page": page, "offset": page_size})
        r.raise_for_status()
        body = r.json()
    msgs = body.get("messages", body) if isinstance(body, dict) else body
    if isinstance(msgs, list):
        return msgs, 1
    return msgs.get("records", []), int(msgs.get("pages") or 1)


def media_base64(wa_id):
    """Re-download a message's attachment through Evolution. Raises if WhatsApp no longer has it."""
    with _client() as h:
        r = h.post(f"/chat/getBase64FromMediaMessage/{INSTANCE}",
                   json={"message": {"key": {"id": wa_id}}, "convertToMp4": False})
        r.raise_for_status()
        return r.json()
