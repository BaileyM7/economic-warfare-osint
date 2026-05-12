# Risk Feed UI Restructure — Design Spec

**Date:** 2026-05-11
**Branch:** `feature/risk-feed-restructure` (from `emissary-v1`)
**Scope:** Frontend-only UI restructure of the Risk Feed page. No backend changes.

## Motivation

Two pieces of feedback prompted this work:

1. **Section ordering on the Risk Feed page does not match how analysts use it.** The Watchlist Manager currently nests three panels — active items, "Track an entity by name", and "Suggested starter items" — inside one big collapsible. The user wanted them as four sibling sections in a different order, with each independently collapsible.
2. **A 6-item watchlist produced 21 cards in one refresh.** Log trace confirmed this is by design, not a bug: 18 of the 21 cards were unconditional OFAC SDN top-ranked surface (runs even with an empty watchlist), and only 3 came from the user's watchlist. The mental model "N watchlist items → ~N cards" is broken without any visual cue distinguishing the two sources.

## Decisions made during brainstorming

| Decision | Choice | Reason |
|---|---|---|
| OFAC unconditional fan-out | **Keep as-is in backend** | Global SDN signals matter even for new users. Don't break the "global surface" mental model. |
| Provenance disclosure | **Per-card badge** ("Global" vs "Watchlist") | Lowest layout impact; user can see provenance at point-of-read on any card. |
| Sibling sections architecture | **Three sibling collapsibles inside `WatchlistManager`** | Matches user-visible "4 panels" model without lifting state out of the component that owns watchlist data + handlers. |
| Section order | Manage Watch-list → Suggested Starter Items → Track an Entity by Name → Tracked Reports | Per user request. |
| Default open/closed | Manage = open, Suggested = closed, Track Entity = open, Reports = always visible | User said Suggested should "fold up to reduce UI noise"; Manage and Track Entity are primary add/edit flows. |
| Persistence of collapse state | **In-memory only** (`useState`) | YAGNI; can add localStorage later if requested. |
| Tests | **None added in this change** | Risk feed has zero tests today; adding the first ones is a separate effort. Manual browser verification only. |

## Architecture

### Frontend component tree (after change)

```
RiskFeedPage
├── <Header> (refresh button, last-refresh meta)
├── <WatchlistManager>            ← still owns shared state for all three sections below
│   ├── <CollapsibleSection title="Manage Watch-list" defaultOpen={true}>
│   │   └── active items grouped by category
│   ├── <CollapsibleSection title="Suggested starter items" defaultOpen={false}>
│   │   └── <SuggestionsPicker> (existing internal component)
│   └── <CollapsibleSection title="Track an entity by name" defaultOpen={true}>
│       └── <EntityResolveBox> (existing internal component)
└── <ReportsGrid> (existing 3-column category layout)
    └── <RiskFeedCard> [...]
        └── <ProvenanceBadge>     ← NEW, top-right of card
```

`WatchlistManager` continues to own:
- `items` (active watchlist rows)
- `suggestions`
- `selectedSuggestions`
- `error`, `loading`, `busyId`, `committing`
- The `load`, `handleDelete`, `handleAddOne`, `handleCommitSelection` handlers
- The `onChanged` callback bubbling up to `RiskFeedPage` so the reports refresh after watchlist changes

The three sub-sections now sit as siblings inside the component instead of nested under one outer collapsible. The single outer `<section>` and outer toggle (lines 173-195 of current `WatchlistManager.tsx`) get removed.

### New shared helper: `frontend/src/lib/cardProvenance.ts`

```ts
export type CardProvenance = 'global' | 'watchlist';

export function cardProvenance(itemId: string): CardProvenance | null {
  if (itemId.startsWith('ofac-') || itemId.startsWith('csl-')) return 'global';
  if (
    itemId.startsWith('yf-') ||
    itemId.startsWith('gdelt-ent-') ||
    itemId.startsWith('gdelt-region-')
  ) return 'watchlist';
  return null;
}
```

- **Returns `null` for unknown prefixes** so future card types fail open (no badge) rather than miscategorize.
- Used by `RiskFeedCard.tsx` (for the badge) and **refactored into `AddToWatchlistButton.tsx`** to replace its existing inline prefix-parsing (CLAUDE.md codebase map flagged this as a coupling hotspot — extracting eliminates it).

### `<ProvenanceBadge>` styling

- Small pill rendered in the card top-right corner, adjacent to the existing severity chip on the same row.
- `Global` → muted slate/outline tone (de-emphasized; these are background signals).
- `Watchlist` → primary-tint background (emphasized; these match the user's curated entities).
- No icon — text-only to keep the existing icon-rich card layout from getting noisier.
- No tooltip for now (badge is self-explanatory; can add `title` attribute later if needed).

### `<CollapsibleSection>` helper

A small inline helper at the top of `WatchlistManager.tsx` (not a separate file) replicating the existing collapsible header pattern (the button-with-chevron from current line 174-195) but parameterized over title, icon, defaultOpen, and children. **Reasoning for not extracting to its own file:** only used in one file in this change; if reused elsewhere later, extract then.

## Data flow (unchanged)

- `WatchlistManager` calls `fetchWatchlist()` + `fetchWatchlistSuggestions()` on mount (existing).
- `handleAddOne` / `handleCommitSelection` / `handleDelete` mutate local `items` state and call `onChanged?.()` to trigger parent refresh (existing).
- `RiskFeedPage` re-fetches the feed on `onChanged` (existing).
- API contract for `/api/risk-feed`, `/api/watchlist`, `/api/watchlist/suggestions` is **unchanged**.

## Error handling (unchanged)

- Existing `error` state in `WatchlistManager` continues to surface inside the first (Manage) section.
- `RiskFeedPage` continues to surface `last_refresh.errors` in its header.
- Provenance badge gracefully renders nothing (`return null`) for unknown id prefixes — no error.

## Testing

- **Pre-commit gate must pass** (`npm run typecheck`, Prettier, ESLint).
- **Manual browser verification** at `http://localhost:5173/risk-feed`:
  1. On initial load, sections render in order: Manage (open) → Suggested (closed) → Track Entity (open) → Reports.
  2. Toggling each section's header opens/closes only that section.
  3. The existing add/delete/resolve flows still work (no state-coordination regressions).
  4. Risk feed cards show `Global` or `Watchlist` badge matching their ID prefix; OFAC cards show `Global`, GDELT/yfinance cards show `Watchlist`.
- No automated tests added in this change.

## Out of scope (YAGNI'd)

- Persisting collapse state to localStorage across page loads.
- Filtering OFAC defaults by watchlist.
- Capping the OFAC default count below current value.
- Header counter showing "N yours · M global" math.
- Automated tests for the risk feed (the file has zero today; this PR does not add the first).
- Sync of `develop` branch from `emissary-v1` (separate hygiene task — flagged for the user).

## File-touch list

| Path | Change type |
|---|---|
| `frontend/src/lib/cardProvenance.ts` | new |
| `frontend/src/components/RiskFeedCard.tsx` | edit — add `<ProvenanceBadge>` and import helper |
| `frontend/src/components/WatchlistManager.tsx` | edit — replace outer collapsible with 3 sibling collapsibles in new order; add inline `CollapsibleSection` helper |
| `frontend/src/components/AddToWatchlistButton.tsx` | edit — replace inline prefix parsing with import from helper |
| `docs/superpowers/specs/2026-05-11-risk-feed-restructure-design.md` | new (this file) |
