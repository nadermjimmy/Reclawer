import { ChartColumn, MessagesSquare } from "lucide-react";
import { useParams } from "react-router-dom";
import { usePageTitle } from "../../app/title";
import { ButtonLink, EmptyState } from "../../components";

/* Part 2 replaces these two screens (routes /chats, /chats/:jid and /insights are already wired in App.tsx). */

export function ChatsPlaceholder() {
  usePageTitle("Chats");
  const { jid } = useParams();
  return (
    <div className="page">
      <div className="card">
        <EmptyState icon={<MessagesSquare size={26} />} title="Coming in the next build">
          <p>Chats will show every WhatsApp chat as a read-only conversation with search, Sync now and the suggestions that came from it.</p>
          {jid && <p className="small">You opened chat <span className="mono">{jid}</span>.</p>}
          <p>Until then, the classic feed has the same messages.</p>
        </EmptyState>
        <div className="empty-actions">
          <a className="btn btn-secondary btn-md" href={jid ? `/feed#${encodeURIComponent(jid)}` : "/feed"}>Open the classic feed</a>
          <ButtonLink to="/updates" variant="ghost">Back to Updates</ButtonLink>
        </div>
      </div>
    </div>
  );
}

export function InsightsPlaceholder() {
  usePageTitle("Insights");
  return (
    <div className="page">
      <div className="card">
        <EmptyState icon={<ChartColumn size={26} />} title="Coming in the next build">
          <p>Insights will answer what changed, what moved and what is going stale, with the two downloads one click away.</p>
        </EmptyState>
        <div className="empty-actions">
          <ButtonLink to="/master">Go to downloads on Master</ButtonLink>
        </div>
      </div>
    </div>
  );
}
