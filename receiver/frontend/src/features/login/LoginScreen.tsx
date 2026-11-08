import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { useSession } from "../../app/session";
import { Button } from "../../components";
import "./login.css";

/** One field, "Team password". Same 14-day session as the classic pages. */
export function LoginScreen() {
  const { login } = useSession();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    document.title = "Log in · Byit Receiver";
    input.current?.focus();
  }, []);
  return (
    <div className="login">
      <form className="login-card" onSubmit={async (e) => {
        e.preventDefault();
        if (!password || busy) return;
        setBusy(true);
        setError(null);
        try {
          await login(password);
        } catch (err) {
          setError(err instanceof ApiError && err.status === 401
            ? "That password didn't work. Check with the owner."
            : "Couldn't reach the server. Check your connection and try again.");
          setBusy(false);
          input.current?.select();
        }
      }}>
        <div className="eyebrow">Byit Receiver</div>
        <h1>Log In</h1>
        <p className="muted small">Sheet updates from the team's WhatsApp chats. Listen-only: nothing is ever sent to WhatsApp.</p>
        <label className="field">
          <span className="field-label">Team password</span>
          <input ref={input} className="input" type="password" autoComplete="current-password" value={password}
            aria-invalid={Boolean(error)} aria-describedby={error ? "login-error" : undefined}
            onChange={(e) => { setPassword(e.target.value); setError(null); }} />
        </label>
        {error && <p id="login-error" className="login-error" role="alert">{error}</p>}
        <Button type="submit" variant="primary" block disabled={busy}>{busy ? "Logging in" : "Log in"}</Button>
      </form>
    </div>
  );
}
