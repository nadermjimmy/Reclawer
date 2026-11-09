import { Columns3 } from "lucide-react";
import { useState } from "react";
import { getTemplate, saveTemplate, type SheetTemplate } from "../../api/brain";
import { errorText } from "../../api/client";
import { Button, ConfirmDialog, useToast } from "../../components";
import { label } from "../../lib/format";

/** Phase 5: a person says which column of this price list holds which sheet field. Later sheets with the same
 *  layout from the same developer are then read without Claude (TEMPLATES=1). Sales columns can't be mapped. */
export function ConfirmColumns({ fileId, sheet }: { fileId: number; sheet: string }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [t, setT] = useState<SheetTemplate | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});

  const start = async () => {
    setBusy(true);
    try {
      const got = await getTemplate(fileId, sheet);
      setT(got);
      setMapping(got.mapping);
      setOpen(true);
    } catch (e) {
      toast.show({ tone: "error", message: `Couldn't load the columns. ${errorText(e)}` });
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    try {
      const got = await saveTemplate(fileId, sheet, mapping);
      setT(got);
      setOpen(false);
      toast.show({ tone: "success", message: got.enabled ? "Columns confirmed. Sheets with this layout are now read without Claude."
        : "Columns confirmed. They're used once TEMPLATES=1 is set on the receiver." });
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e) });
    } finally {
      setBusy(false);
    }
  };

  const valid = Object.values(mapping).includes("project") && Object.values(mapping).some((v) => v && !["project", "developer", "location"].includes(v));
  return (
    <>
      <Button size="sm" variant="ghost" icon={<Columns3 size={14} />} disabled={busy} onClick={() => void start()}>
        {t?.confirmed ? "Edit Columns" : "Confirm Columns"}
      </Button>
      <ConfirmDialog open={open} title={`Columns of ${sheet}`} confirmLabel="Save Columns" busy={busy || !valid}
        onConfirm={() => void save()} onCancel={() => setOpen(false)}>
        <p className="small muted">Pick what each column holds. Map the project column and at least one value column. Columns left
          blank are ignored; sales names and numbers are never read.</p>
        <div className="colmap">
          {(t?.headers ?? []).map((h, i) => (h.trim() ? (
            <label key={i} className="field colmap-row">
              <span className="field-label" dir="auto">{h}</span>
              <select className="input" value={mapping[String(i)] ?? ""}
                onChange={(e) => setMapping((m) => ({ ...m, [String(i)]: e.target.value }))}>
                <option value="">Ignore</option>
                {(t?.roles ?? []).map((r) => <option key={r} value={r}>{label(r)}</option>)}
              </select>
            </label>
          ) : null))}
        </div>
      </ConfirmDialog>
    </>
  );
}
