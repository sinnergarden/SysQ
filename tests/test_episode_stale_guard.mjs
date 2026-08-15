// P0.3 — the frontend stale-async guard must reject a response unless BOTH the
// request token still matches AND the requested run is still the active run.
//
// app.js is a classic script (load-time DOM access), so it cannot be
// vm.runInNewContext()'d whole; we extract the pure decision function by
// regex and evaluate it in isolation.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const appJsPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'qsys', 'research_ui', 'web', 'app.js');
const src = readFileSync(appJsPath, 'utf8');

const m = src.match(/function isEpisodeResponseStale\(\{ seq, requestedRunId, requestSeq, activeRunId \}\) \{[^}]*\}/);
if (!m) {
  throw new Error('isEpisodeResponseStale not found in app.js (P0.3 guard missing?)');
}
const isEpisodeResponseStale = new Function(`${m[0]}; return isEpisodeResponseStale;`)();

let passed = 0;
const failures = [];
function check(name, actual, expected) {
  if (actual === expected) {
    passed += 1;
  } else {
    failures.push(`${name}: expected ${expected}, got ${actual}`);
  }
}

// Same token, same active run → response may write state.
check('current request + same run is fresh', isEpisodeResponseStale({ seq: 3, requestedRunId: 'R1', requestSeq: 3, activeRunId: 'R1' }), false);

// A newer episodes request superseded this one → stale even if the run is unchanged.
check('superseded request is stale', isEpisodeResponseStale({ seq: 2, requestedRunId: 'R1', requestSeq: 3, activeRunId: 'R1' }), true);

// The run switched while this request was in flight but no fresh episodes
// request has fired yet (loadBacktest is still fetching summary/daily) →
// must NOT write this run's panel.
check('run switched before fresh request is stale', isEpisodeResponseStale({ seq: 3, requestedRunId: 'R1', requestSeq: 3, activeRunId: 'R2' }), true);

// Both mismatches at once.
check('superseded request + switched run is stale', isEpisodeResponseStale({ seq: 2, requestedRunId: 'R1', requestSeq: 4, activeRunId: 'R2' }), true);

// Same token, same run, different requestedRunId string — still fresh (no false positive on equality).
check('run string equality is not confused', isEpisodeResponseStale({ seq: 1, requestedRunId: 'R1', requestSeq: 1, activeRunId: 'R1' }), false);

if (failures.length) {
  console.error('FAILED:\n  ' + failures.join('\n  '));
  process.exit(1);
}
console.log(`isEpisodeResponseStale: ${passed} checks passed`);
