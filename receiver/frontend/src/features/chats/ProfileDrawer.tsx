import { useEffect, useState } from "react";
import { errorText } from "../../api/client";
import { getProfile, saveProfile, type ProfileInfo } from "../../api/chats";
import { Button, Drawer, ErrorBanner, Skeleton, useToast } from "../../components";

/** Developer profile of a chat: whose group it is and how that developer shares data. Read by extraction,
 * matching, the brain and the Data Missing insight. */
export function ProfileDrawer({ jid, chatName, open, onClose, onSaved }: {
  jid: string;
  chatName: string;
  open: boolean;
  onClose: () => void;
  onSaved: (developer: string) => void;
}) {
  const toast = useToast();
  const [info, setInfo] = useState<ProfileInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [developer, setDeveloper] = useState("");
  const [shares, setShares] = useState("");
  const [onRequest, setOnRequest] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setInfo(null);
    getProfile(jid).then((i) => {
      setInfo(i);
      setDeveloper(i.profile?.developer ?? "");
      setShares(i.profile?.shares ?? "");
      setOnRequest(i.profile?.on_request ?? []);
      setNotes(i.profile?.notes ?? "");
      setError(null);
    }).catch((e) => setError(errorText(e, "Couldn't load the profile.")));
  }, [open, jid]);

  const save = async () => {
    setBusy(true);
    try {
      const r = await saveProfile(jid, { shares, on_request: onRequest, notes });
      toast.show({ message: "Developer profile saved" });
      onSaved(r.profile.developer);
      onClose();
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e, "Couldn't save. Try again.") });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer open={open} onClose={onClose} eyebrow="Developer profile" title={chatName}
      footer={<div className="row"><Button variant="primary" disabled={busy || !info} onClick={() => void save()}>Save Profile</Button>
        <Button variant="ghost" onClick={onClose}>Cancel</Button></div>}>
      {error && <ErrorBanner>{error}</ErrorBanner>}
      {!info && !error ? <Skeleton height={200} /> : info && (
        <div className="profile-form">
          <p className="small muted">How this developer shares data. The brain and the Data Missing insight read it.</p>
          <p className="small"><span className="muted">Developer:</span> <b dir="auto">{developer || "Not assigned"}</b>
            <span className="caption muted"> (set it in the bar above the chat)</span></p>
          <fieldset className="field">
            <legend className="field-label">What they send</legend>
            {[["", "Not set"], ["inventory", "Full inventory"], ["starting_prices", "Starting prices only"]].map(([v, l]) => (
              <label key={v} className="radio-row"><input type="radio" name="shares" checked={shares === v} onChange={() => setShares(v)} /> {l}</label>
            ))}
          </fieldset>
          <fieldset className="field">
            <legend className="field-label">Only sent on request</legend>
            <div className="check-grid">
              {info.unit_types.map((u) => (
                <label key={u.field} className="radio-row">
                  <input type="checkbox" checked={onRequest.includes(u.field)}
                    onChange={(e) => setOnRequest((o) => e.target.checked ? [...o, u.field] : o.filter((x) => x !== u.field))} />
                  {u.label}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="field">
            <span className="field-label">Notes for the brain</span>
            <textarea className="input" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} dir="auto"
              placeholder="e.g. Soma Bay is also the location of all their projects" />
          </label>
        </div>
      )}
    </Drawer>
  );
}
