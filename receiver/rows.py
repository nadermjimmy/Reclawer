"""Which stored files belong to the chats the app reads."""
import db


def scoped_file_ids(c):
    """Files posted at least once in an in-scope group."""
    scoped = db.scoped_groups(c)
    if not scoped:
        return set()
    q = ("SELECT DISTINCT o.file_id FROM file_occurrences o JOIN source_messages m ON m.wa_id=o.wa_id "
         f"WHERE m.group_jid IN ({','.join('?' * len(scoped))})")
    return {r[0] for r in c.execute(q, scoped)}
