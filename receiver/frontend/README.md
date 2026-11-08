# Receiver admin app (React)

The new admin app, served by FastAPI at `/app` (the classic Jinja pages stay at `/`). Spec: `docs/ux-revamp.md`;
visual system: Byit Design System (light theme), tokens in `src/styles/tokens.css`.

## Dev

```bash
# terminal 1: the backend (from receiver/)
DB_PATH=/tmp/rcv/messages.db WEBHOOK_TOKEN=t APP_PASSWORD=pw EVOLUTION_URL=http://x EVOLUTION_API_KEY=k \
  EVOLUTION_INSTANCE=i MARKET=eg python -m uvicorn app:app --port 8766
# terminal 2: the app with hot reload (proxies /api and /sources/file to :8766)
npm ci && npm run dev        # http://localhost:5173/app/
```

Log in with `APP_PASSWORD`. The session cookie is the same one the classic pages use.

## Build

`npm run build` type-checks and writes `dist/` (base `/app/`). FastAPI serves `dist/index.html` for every `/app/*`
route and the hashed files under `/app/assets/`. Without a build, `/app` shows a short "not built" note and
nothing else breaks. The Docker image builds the app in a node stage (see `receiver/Dockerfile`).
Dependencies are pinned to exact versions; update them deliberately and commit `package-lock.json`.

## Structure

```
src/
  api/            typed client (client.ts) + one module per resource: session, proposals, master, jobs,
                  legacy (inventory/review/sources), chats (list, messages, search, sync), insights
  app/            App.tsx (routes + legacy redirects), Shell.tsx (rail, top bar, tab bar), session.tsx
                  (/api/me polling, login), jobs.tsx (job pill, log drawer, startStep, queueWhenFree),
                  nav.ts (navigation items + badges), title.tsx (usePageTitle)
  components/     shared UI: Button/IconButton/ButtonLink, Pill/StatePill, Chip/WarningChip (WARNING_META),
                  Card/Stat, Toast, Drawer, ConfirmDialog, KeyboardHelp, Segmented, Select/SearchInput,
                  EmptyState/ErrorBanner/InfoBanner, Skeleton, Table
  features/<screen>/   one folder per screen (+ its css); chats/ (list | conversation | suggestions panel,
                  useChatSync), insights/, palette/ (Ctrl/⌘K)
  lib/            format.ts (numbers with units, dates, relTime), hooks.ts (useLoad, useHotkeys, useDelayed, ...),
                  text.ts (Arabic-aware normalise/match/highlight, same rules as receiver/search.py)
  motion.ts       motion tokens + Framer Motion variants (120/200/360 ms, no springs)
  styles/         tokens.css, base.css, components.css
```

## Adding a screen

1. Add API calls + types in `src/api/<resource>.ts` (use `get`/`post` from `client.ts`; errors are `ApiError`,
   use `errorText(e)` for copy). Add the backend route in `receiver/api.py` and a test in `tests/test_api.py`.
2. Create `src/features/<screen>/<Screen>.tsx`; call `usePageTitle("Title")`; load with `useLoad`; use the shared
   components; skeletons only via `useDelayed`; screen CSS next to it, built from tokens only.
3. Register the route in `src/app/App.tsx`, and a nav entry in `src/app/nav.ts` if it needs one.
4. Keyboard: `useHotkeys({ key: fn })` (ignored while typing or when a dialog is open); list keys in a
   `KeyboardHelp` sheet.
5. Motion: use variants from `motion.ts`; the app is wrapped in `MotionConfig reducedMotion="user"`.

Rules from the design system: pill radius for every button/chip/input, cards 20px with the soft shadow, raised
shadow only for drawers/dialogs/toasts, navy for structure and orange for the primary action, no gradients,
blur or emoji, `dir="auto"` on message text, quotes and project names, numbers with units and `tnum`.
