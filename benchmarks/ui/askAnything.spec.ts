/**
 * Before/after capture of the real "Ask Anything" experience.
 *
 * Logs in via the UI, submits a demo query, and measures the TRUE user-perceived
 * wall-clock (submit → "Executive Assessment" rendered). Captures a screenshot +
 * video (the before/after picture) and the completed assessment JSON (via network
 * intercept) so richness is scored from the same payload the API harness uses.
 *
 * Env: BASE_URL, EMISSARY_USER, EMISSARY_PASS, BENCH_QUERY (default: the warmed
 * Fujian Jinhua demo query), BENCH_LABEL (before|after — names the output files).
 *
 * Scope: the current UI sends only {query} and ignores session_id/replayed_from,
 * so this fairly captures analyze + suggest only. Memory/session before/after
 * lives in the API harness until the frontend surfaces those.
 */
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const USER = process.env.EMISSARY_USER || 'demo';
const PASS = process.env.EMISSARY_PASS || 'demo123';
const LABEL = process.env.BENCH_LABEL || 'run';
const QUERY =
  process.env.BENCH_QUERY ||
  'What happens to global semiconductor supply if we sanction Fujian Jinhua?';

const RESULTS = path.join(__dirname, '..', 'results', 'ui');
fs.mkdirSync(RESULTS, { recursive: true });

async function login(page) {
  await page.goto('/login');
  // Inputs identified by autocomplete attributes (stable across restyles).
  await page.fill('input[autocomplete="username"]', USER);
  await page.fill('input[autocomplete="current-password"]', PASS);
  await page.getByRole('button', { name: /log ?in|sign ?in/i }).click();
  // Land on the search page.
  await page.waitForURL(/\/search/, { timeout: 30_000 });
}

test('Ask Anything — analyze latency + richness', async ({ page }) => {
  // Capture the completed analysis payload straight off the wire.
  let completed: any = null;
  page.on('response', async (resp) => {
    if (/\/api\/analyze\/[^/]+$/.test(resp.url()) && resp.request().method() === 'GET') {
      try {
        const body = await resp.json();
        if (body?.status === 'completed' && body?.result) completed = body;
      } catch {
        /* not JSON yet */
      }
    }
  });

  await login(page);

  const ask = page.getByPlaceholder(/what happens to global semiconductor supply/i);
  await ask.fill(QUERY);

  const t0 = Date.now();
  await page.getByRole('button', { name: /run deep analysis/i }).click();

  // Completion = the Executive Assessment heading appears.
  await expect(page.getByText(/executive assessment/i)).toBeVisible({ timeout: 14 * 60 * 1000 });
  const elapsedS = (Date.now() - t0) / 1000;

  // The picture.
  await page.screenshot({
    path: path.join(RESULTS, `askAnything-${LABEL}.png`),
    fullPage: true,
  });

  // Richness from the intercepted payload (fallback: structural fields on screen).
  const result = completed?.result ?? null;
  const richness = result
    ? {
        present: true,
        executive_summary_chars: (result.executive_summary || '').length,
        findings: (result.findings || []).length,
        sources: (result.sources || []).length,
        entities: (result.entity_graph?.entities || []).length,
        relationships: (result.entity_graph?.relationships || []).length,
        recommendations: (result.recommendations || []).length,
      }
    : { present: false };

  const out = {
    label: LABEL,
    query: QUERY,
    baseURL: process.env.BASE_URL || 'http://localhost:8000',
    perceived_latency_s: Math.round(elapsedS * 10) / 10,
    was_replay: Boolean(completed?.replayed_from),
    richness,
  };
  fs.writeFileSync(
    path.join(RESULTS, `askAnything-${LABEL}.json`),
    JSON.stringify(out, null, 2),
  );

  console.log(`[${LABEL}] perceived latency ${out.perceived_latency_s}s · richness`, richness);
  expect(result, 'completed assessment payload should have been intercepted').not.toBeNull();
});

test('Suggest — "Did you mean?" on a reworded query', async ({ page }) => {
  await login(page);
  const reworded = 'What sanctions exposures does Nuctech have, and who ultimately owns it?';
  const ask = page.getByPlaceholder(/what happens to global semiconductor supply/i);
  await ask.fill(reworded);

  // The suggest chip fires on the run action (handleDeep calls suggest first).
  // Give it a short window to render the "Did you mean" chip; absence is a valid
  // (BEFORE) result, not a failure.
  await page.getByRole('button', { name: /run deep analysis/i }).click();
  const chip = page.getByText(/did you mean/i);
  const shown = await chip
    .waitFor({ state: 'visible', timeout: 8_000 })
    .then(() => true)
    .catch(() => false);

  const out = { label: LABEL, query: reworded, suggest_chip_shown: shown };
  fs.writeFileSync(
    path.join(RESULTS, `suggest-${LABEL}.json`),
    JSON.stringify(out, null, 2),
  );
  console.log(`[${LABEL}] suggest chip shown: ${shown}`);
});
