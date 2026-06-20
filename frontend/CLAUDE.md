# Frontend — Repo-Level Context

**This file loads automatically into every Claude Code session inside `frontend/`.**
For setup commands and pre-commit hook details, see [README.md](README.md).
This file covers things that aren't in the README: the structure, the
backend integration boundary, the dual-domain split (Emissary vs. wargame
sub-app), and the things that have caused real bugs.

This repo is consumed as a **git submodule** by the parent Emissary
backend at [github.com/BaileyM7/economic_warfare](https://github.com/BaileyM7/economic_warfare).
Changes here are picked up by the parent repo on submodule update.

---

## What this is

A React 18 + Vite + TypeScript single-page app that consumes the Emissary
FastAPI backend. The UI has two distinct surfaces:

| Surface              | Routes                                                                 | Data                                                                                  |
| -------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| **Emissary** (OSINT) | `/risk-feed`, `/search`, `/coa`, `/monitoring`, `/briefings`, `/admin` | Emissary backend at `/api/*`                                                          |
| **Wargame**          | `/wargame`                                                             | Embedded swarm subapp at `/api/wargame/*` (only when backend has `WARGAME_ENABLED=1`) |

The wargame surface is **isolated under [src/wargame/](src/wargame/)** with its
own components, lib, and store. Vite aliases `@` → `src/wargame` ([vite.config.ts:7-10](vite.config.ts#L7-L10))
so wargame code can import via `@/components/...`. **Don't use that alias
from non-wargame code** — it crosses a boundary that's deliberate.

---

## Source-tree map

```
src/
  App.tsx               # Top-level routes + auth gates (RequireAuth, RequireAdmin)
  api.ts                # Single source of truth for Emissary API calls (typed wrappers around fetch)
  types.ts              # TypeScript mirrors of backend Pydantic models — keep in lockstep with src/common/types.py
  main.tsx              # ReactDOM root + router provider
  index.css             # Tailwind v4 entry; design tokens live here
  layout/
    AppShell.tsx        # Outer chrome (sidenav + topnav + statusbar) — every authed page renders inside this
    SideNav.tsx
    TopNav.tsx
    StatusBar.tsx
  pages/                # One file per route in App.tsx — lazy-loaded
  components/           # Shared widgets (charts, panels, cards) — Emissary-side only
  hooks/                # useAuth, useMonitoringSocket, useSearchAnalysis
  wargame/              # ENTIRELY SEPARATE — mirror of swarm/src/frontend, vendored here
    components/
    hooks/
    lib/
      api/              # Wargame-specific fetch + WS client
      store/            # Wargame Zustand store (NOT shared with Emissary state)
      types/            # Mirrors swarm/src/shared/schemas
```

---

## Backend integration: what to know

### API base URL is environment-configured

[api.ts:26-29](src/api.ts#L26-L29):

```ts
const API_BASE = ((import.meta.env.VITE_API_BASE_URL as string | undefined) || '').replace(
  /\/$/,
  '',
);
```

- **Dev:** unset → empty string → relative paths → Vite proxy ([vite.config.ts:12-19](vite.config.ts#L12-L19))
  rewrites `/api/*` to `http://localhost:8000`. Same for `/ws/*`.
- **Prod:** `VITE_API_BASE_URL` is set at **build time** to the Render
  backend URL. Bake it into the static assets via the build env, not at runtime.

### Auth is a Bearer token in localStorage

[api.ts:33-50](src/api.ts#L33-L50). The token is set on login, read into the
`Authorization` header on every authed call, and cleared + redirect-to-login
on a 401. **Never store anything sensitive in localStorage besides this token** —
XSS exfiltration is the threat model and the token rotates on logout.

### The single fetch convention

All Emissary API calls go through `authedFetch` + `parseJson<T>` in
[api.ts](src/api.ts). When adding a new endpoint:

1. Add the response type to [types.ts](src/types.ts) (mirror the backend
   Pydantic model exactly — name, optional fields, nested shapes).
2. Add a typed wrapper to [api.ts](src/api.ts) that calls `authedFetch` and
   `parseJson<TheNewType>`.
3. Components import the wrapper. **Don't `fetch()` directly from a component.**

This keeps auth header injection, 401 handling, and JSON parsing in one place.

---

## Routing & lazy loading

[App.tsx](src/App.tsx) defines all routes. **Every page is `lazy()`-imported**
to keep the initial bundle small — heavy pages (Wargame, Monitoring) load on
demand. Don't add a top-level eager import for a page; it'll bloat the
initial bundle and erase the lazy-loading benefit for everyone.

`RequireAuth` (no token → redirect to `/login`) wraps the AppShell.
`RequireAdmin` additionally checks the `useAuth().isAdmin` flag — fetched
from the backend on mount. Adding a new admin-only page: wrap it in
`<RequireAdmin>`, don't invent a parallel mechanism.

---

## State management split

The two surfaces use **different state libraries** for different reasons:

| Surface  | Library                          | Where                                                               |
| -------- | -------------------------------- | ------------------------------------------------------------------- |
| Emissary | SWR (server cache) + React state | `useSearchAnalysis`, `useMonitoringSocket`, in-component `useState` |
| Wargame  | Zustand store                    | [src/wargame/lib/store/](src/wargame/lib/store/)                    |

The wargame side is a near-direct port of the standalone swarm frontend
which uses Zustand throughout; touching that side, follow swarm conventions.
The Emissary side prefers SWR for fetched data — don't introduce a global
store just for one page's data.

---

## Tailwind v4 (note the version)

[package.json](package.json) pins `tailwindcss@^4.2.2` and uses the new
`@tailwindcss/postcss` plugin. **This is Tailwind v4, not v3** — the config
is in CSS via `@theme` blocks in [src/index.css](src/index.css), not in
`tailwind.config.js`. v3-style snippets pasted from Stack Overflow won't
work; check the v4 migration guide before troubleshooting.

---

## Pre-commit gate

[.husky/pre-commit](.husky/pre-commit) runs:

1. `lint-staged` — Prettier + ESLint --fix on staged files
2. `npm run typecheck` — `tsc --noEmit` over the whole project

A new TS error anywhere in the tree blocks the commit even if your changes
were elsewhere. **This is intentional.** The CI's `frontend-build` job is
the second line of defense (full `tsc + vite build`); pre-commit is the
faster local feedback loop.

---

## What NOT to do

1. **Don't import from `src/wargame/` outside of `WargamePage.tsx`** —
   that boundary is what lets the wargame side stay pluggable / removable.
2. **Don't bypass `authedFetch`/`parseJson<T>`.** A bare `fetch()` skips
   auth headers, 401 redirects, and our error normalization.
3. **Don't read `import.meta.env.VITE_API_BASE_URL` outside of [api.ts](src/api.ts).**
   The single read site means there's one place to change if the URL
   convention shifts.
4. **Don't drift `types.ts` from the backend.** A frontend type that says
   `confidence: number` while the backend returns `Confidence` (enum) means
   silent wrongness in production. When the Pydantic model changes, update
   `types.ts` in the same PR.
5. **Don't add to `RequireAuth`/`RequireAdmin` — wrap your route in them.**
   Forks of the auth guard bypass the redirect logic that's tested.
6. **Don't put eager top-level imports for new pages.** Use `lazy()`.
7. **Don't paste Tailwind v3 config into `tailwind.config.js`.** v4 reads
   from CSS `@theme`. Adding a v3-shaped config silently does nothing,
   and you'll think the styles are broken.

---

## When something breaks

| Symptom                                                   | First thing to check                                                                                                                                                                                                  |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Login works but every API call returns 401                | `VITE_API_BASE_URL` mismatch — frontend hitting wrong backend, or backend's `CORS_ORIGINS` doesn't include the frontend's origin (browser blocks the preflight, you see no `Authorization` header on the server side) |
| Wargame page loads but no data, no errors                 | Backend's `WARGAME_ENABLED` is unset → `/api/wargame/*` 404s. Check parent repo's render env vars.                                                                                                                    |
| `npm run typecheck` errors mention files you didn't touch | Someone else's drift from `types.ts` ↔ backend. Run `git diff main -- src/types.ts` to see if the type was just updated.                                                                                              |
| Build succeeds, page renders blank in prod                | Lazy-load failed silently due to misnamed chunk. Check browser console; verify route path matches `App.tsx` and the `lazy(() => import('./pages/...'))` path.                                                         |
| Vite dev server CORS errors                               | `vite.config.ts` proxy not catching the path. The proxy only rewrites `/api` and `/ws` — anything else, the browser tries to hit the dev server origin directly.                                                      |
