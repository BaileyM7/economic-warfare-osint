# 07 — Frontend (`frontend/`, vendored)

The **React SPA** for the whole product, built to `frontend/dist/` and served statically by the
Python backend. It is **vendored** into this repo as regular tracked files — no longer a git
submodule. Its upstream was `github.com/deveshkumars/economicgamingv1` (branch `emissary-v1`);
vendoring decouples from that upstream, so future UI changes happen directly in this repo.

> No submodule checkout is needed — the source is already in-repo. CI's `frontend-build` job and
> `build.sh` both build it.

## Stack

- **React 18 + Vite 5 + TypeScript** (strict), **React Router 7** (history mode).
- **Styling:** Tailwind CSS **v4** (CSS-based `@theme` config in `src/index.css`; no `tailwind.config.js`).
- **State:** **SWR** for Emissary server-state; **Zustand** for the wargame simulation store.
- **Visualization:** **Deck.gl** (wargame 3D globe), **Cytoscape.js** (entity graphs —
  clustered community boxes, sized/risk-colored nodes; swapped in from vis-network in issue #35),
  **chart.js** (impact/Sankey), Leaflet (maps). PDF export via `html2canvas` + `jspdf`.

## Scripts (`package.json`)

| Command | Purpose |
|---------|---------|
| `npm run dev` | Vite dev server on **5173**, proxies `/api`→`localhost:8000`, `/ws`→`ws://localhost:8000` |
| `npm run build` | `tsc` (typecheck) then `vite build` → `dist/` |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` / `format` | ESLint 9 (flat config) / Prettier |

## App shell & routes

Entry `src/main.tsx` → `BrowserRouter` → `src/App.tsx`. Pages are **lazy-loaded**. Layout is
`src/layout/AppShell.tsx` (TopNav + 240px SideNav + StatusBar).

| Route | Page | Guard |
|-------|------|-------|
| `/login` | LoginPage | public |
| `/risk-feed` | RiskFeedPage | auth (default redirect from `/`) |
| `/search` | SearchPage | auth |
| `/coa` | COAWorkspacePage | auth |
| `/monitoring` | MonitoringPage | auth |
| `/briefings` | BriefingsPage | auth |
| `/wargame` | WargamePage | auth |
| `/admin` | AdminPage | **admin** (`RequireAdmin`) |

## API integration

- **`src/api.ts`** — the single typed client. `authedFetch` injects the Bearer token from
  `localStorage["emissary_token"]`; a `401` clears it and redirects to `/login`. Response types
  live in **`src/types.ts`** and mirror the backend Pydantic models — **keep them in sync when
  backend models change** (same PR).
- **Base URL:** `import.meta.env.VITE_API_BASE_URL` — empty in prod (relative, same-origin), set
  to `http://localhost:8000` in dev (or via the dev proxy).
- Every backend endpoint the SPA calls corresponds to the catalog in
  [03-backend-and-api.md](../03-backend-and-api.md).

## The wargame UI (`src/wargame/`)

A self-contained subtree (the `@` path alias points here). Talks to the **wargame subapp** (not
the Emissary API):

- **API client:** `src/wargame/lib/api/client.ts`, base `/api/wargame` (override with
  `VITE_SWARM_API_URL`).
- **Live stream:** `src/wargame/hooks/useSimStream.ts` opens `wss://<host>/api/wargame/ws/simulations/{id}`
  (override `VITE_SWARM_WS_URL`), parses frames into the **Zustand `simStore`**, reconnects with backoff.
- **Globe:** `src/wargame/components/Globe/` (Deck.gl arcs/country layers).
- **Scripted demo:** `src/wargame/lib/demo/taiwanDemo.ts` — the **PLAY DEMO** button replays a
  client-side Taiwan-2027 sequence with **no backend** (always works, even when the wargame is off).
- See [swarm-wargame.md](swarm-wargame.md) for the backend contract these consume.

## How it's served in production

`vite build` → `frontend/dist/{index.html, assets/}`. The backend mounts `/assets` and serves
`index.html` for `/` and any unmatched path (SPA fallback). History-mode routing means clean URLs
(`/search`, not `/#/search`) — the backend's catch-all is what makes that work.

## Gotchas

- **Wargame tab with no data** → backend `WARGAME_ENABLED` is off (the page still loads; the demo still works).
- **Type errors in files you didn't touch** → `src/types.ts` drifted from the backend schema.
- **Tailwind styles missing** → v3 syntax used against the v4 CSS config; use `@theme` in `index.css`.
- Only `/api` and `/ws` are proxied in dev — other absolute paths won't hit the backend.
