# Economic Warfare OSINT — Frontend

React + Vite + TypeScript UI for the Economic Warfare OSINT platform.

## Development setup

```sh
npm install
npm run dev
```

`npm install` runs the `prepare` script, which installs husky's pre-commit hook automatically. No further setup is needed.

## Pre-commit hook

Every commit triggers `.husky/pre-commit`, which runs:

1. **`lint-staged`** — Prettier (`prettier --write`) + ESLint (`eslint --fix`) on staged files only.
2. **`npm run typecheck`** — `tsc --noEmit` across the whole project.

Commits with type errors or non-auto-fixable lint errors are rejected.

To bypass in a genuine emergency:

```sh
git commit --no-verify
```

Use sparingly — the hook exists because we previously had inconsistent formatting in commit history.

## Useful scripts

| Command                | Purpose                           |
| ---------------------- | --------------------------------- |
| `npm run dev`          | Vite dev server                   |
| `npm run build`        | Type-check + production build     |
| `npm run preview`      | Preview the production build      |
| `npm run typecheck`    | `tsc --noEmit`                    |
| `npm run lint`         | ESLint over the project           |
| `npm run format`       | Prettier write across the project |
| `npm run format:check` | Prettier check (no writes)        |
