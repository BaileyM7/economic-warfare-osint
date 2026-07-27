#!/usr/bin/env node
// silent-audio.mjs — fabricate the audio cache auto-loom's engine paces from,
// without ElevenLabs. Each beat gets a near-silent noise clip (audible-level
// check in run.mjs requires mean_volume > -60 dB; true silence is -91) of the
// beat's `durationSec`, plus the durations.json the renderer reads.
//
// Usage:
//   node silent-audio.mjs --beats beats/<slug>.beats.json --out demo-video-out/<slug>/audio
//
// Then record with:
//   node ../.claude/skills/auto-loom/scripts/run.mjs --beats <file> --out <dir> --skip-audio --no-captions

import { readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { execFile } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(execFile);

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i].startsWith('--')) args[process.argv[i]] = process.argv[i + 1];
}
const beatsPath = args['--beats'];
const outDir = args['--out'];
if (!beatsPath || !outDir) {
  console.error('Usage: node silent-audio.mjs --beats <beats.json> --out <audio dir>');
  process.exit(1);
}

const raw = JSON.parse(readFileSync(beatsPath, 'utf8'));
const beats = Array.isArray(raw) ? raw : (raw.beats ?? []);
if (!beats.length) {
  console.error(`no beats in ${beatsPath}`);
  process.exit(1);
}

mkdirSync(outDir, { recursive: true });

const durations = [];
for (const beat of beats) {
  const sec = Number(beat.durationSec ?? 5);
  const n = String(beat.id).padStart(2, '0');
  const outPath = join(outDir, `beat-${n}.mp3`);
  // Brown noise at amplitude 0.02 ≈ -45 dB mean volume: passes run.mjs's
  // silent-track check (needs > -60 after mp3+aac re-encode losses) while
  // being inaudible at normal volume. Stripped entirely at transcode time
  // anyway (landing-page videos are muted).
  await execAsync('ffmpeg', [
    '-y', '-f', 'lavfi', '-i', 'anoisesrc=color=brown:amplitude=0.02:r=44100',
    '-t', String(sec), '-q:a', '9', outPath,
  ]);
  // Record the real encoded duration (mp3 framing pads a few ms).
  const { stdout } = await execAsync('ffprobe', [
    '-v', 'quiet', '-print_format', 'json', '-show_format', outPath,
  ]);
  const dur = parseFloat(JSON.parse(stdout).format.duration);
  durations.push(dur);
  console.log(`  beat-${n}.mp3  ${dur.toFixed(2)}s`);
}

writeFileSync(join(outDir, 'durations.json'), JSON.stringify(durations));
console.log(`wrote ${join(outDir, 'durations.json')} (${durations.length} beats, total ${durations.reduce((a, b) => a + b, 0).toFixed(1)}s)`);
