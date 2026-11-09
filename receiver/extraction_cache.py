"""Extraction cache (Phase 5): reuse what Claude already extracted when the same content is read again.

Files are stored once per sha256 and read once (processed_batches), so this pays off on RE-reads (redo, Re-read
all, a new model version) and on forwarded text messages:
- file requests (sheet rows, PDF pages, images): key = hash(model + system prompt + the request content);
- text messages, per message: key = hash(model + system prompt + normalized text + the chat's developer). The same
  text in another developer's group means something else, so it only matches within the same developer (or the
  same chat when the chat has no developer).
Behind EXTRACTION_CACHE=1 (default off).
"""
import hashlib, json, os, time
import db, normalize

ENABLED = os.getenv("EXTRACTION_CACHE", "0") == "1"
MIN_TEXT = 20  # shorter messages ('ok', 'تمام') are cheap and ambiguous: never cached


def _h(*parts):
    return hashlib.sha256("\x00".join(str(p) for p in parts).encode()).hexdigest()


def unit_key(model, system, content):
    return "u:" + _h(model, _h(system), json.dumps(content, ensure_ascii=False, sort_keys=True))


def message_key(model, system, text, developer, jid):
    norm = normalize.normalize(text)
    if len(norm) < MIN_TEXT:
        return None
    scope = normalize.compact(developer) or f"chat:{jid}"
    return "m:" + _h(model, _h(system), norm, scope)


def get(c, key):
    if not key:
        return None
    r = c.execute("SELECT output_json FROM extraction_cache WHERE content_key=?", (key,)).fetchone()
    if not r:
        return None
    c.execute("UPDATE extraction_cache SET hits=hits+1 WHERE content_key=?", (key,))
    return json.loads(r[0])


def put(c, key, kind, developer, model, updates):
    if key:
        c.execute("INSERT OR REPLACE INTO extraction_cache(content_key,source_kind,chat_developer_key,model,output_json,"
                  "created_at,hits) VALUES(?,?,?,?,?,?,0)",
                  (key, kind, normalize.compact(developer), model, json.dumps(updates, ensure_ascii=False),
                   int(time.time())))


def save_unit(u, updates):
    """After Claude answered unit `u`: remember the answer (whole request, or per message for text)."""
    meta = u.get("cache")
    if not ENABLED or not meta:
        return
    with db.connect() as c:
        if meta.get("unit"):
            put(c, meta["unit"], u["stage"], meta.get("developer", ""), meta.get("model") or u["model"], updates)
        for wa, key in (meta.get("messages") or {}).items():
            put(c, key, "message", meta.get("developer", ""), meta.get("model") or u["model"],
                [dict(x, source_id="") for x in updates if x.get("source_id") == wa])


def with_source(updates, source_id):
    return [dict(x, source_id=source_id) for x in updates]
