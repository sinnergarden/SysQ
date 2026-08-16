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

const sm = src.match(/function isCaptureScatterEligible\(item\) \{[^}]*\}/);
if (!sm) {
  throw new Error('isCaptureScatterEligible not found in app.js (Capture Scatter eligibility filter missing?)');
}
const isCaptureScatterEligible = new Function(`${sm[0]}; return isCaptureScatterEligible;`)();

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

// Capture Scatter eligibility — a complex episode (capture_eligible=false) must
// NEVER enter the MFE→Realized-Capture scatter, even though it is closed and
// has both MFE and realized_return.  The predicate must require all four gates:
// closed, capture_eligible===true, MFE present, realized_return present.
const baseEligible = { exit_reason: 'winner_trailing', capture_eligible: true, MFE: 0.2, realized_return: 0.1 };
check('simple round trip enters scatter', isCaptureScatterEligible(baseEligible), true);
check('capture_eligible=false excluded (complex episode)', isCaptureScatterEligible({ ...baseEligible, capture_eligible: false }), false);
check('capture_eligible missing excluded', isCaptureScatterEligible({ ...baseEligible, capture_eligible: undefined }), false);
check('open episode excluded', isCaptureScatterEligible({ ...baseEligible, exit_reason: 'open' }), false);
check('missing MFE excluded', isCaptureScatterEligible({ ...baseEligible, MFE: null }), false);
check('missing realized_return excluded', isCaptureScatterEligible({ ...baseEligible, realized_return: null }), false);
check('unrealized-only excluded (no realized_return)', isCaptureScatterEligible({ ...baseEligible, realized_return: null, unrealized_return: 0.05 }), false);

// Fix #4 — truncated-detail visibility: the response meta must be captured
// (so the UI can tell the detail rows were truncated) and the render path must
// surface a "detail charts use N / total" note when meta.truncated is true.
if (!src.includes('state.backtest.episodeMeta = (payload && payload.meta) || {}')) {
  failures.push('episodeMeta not captured from response payload.meta');
} else {
  passed += 1;
}
const truncRe = /meta\.truncated[^]*?detail charts use \$\{shown\} \/ \$\{total\}/;
if (!truncRe.test(src)) {
  failures.push('truncated detail-visibility note not wired into renderEpisodeAnalytics');
} else {
  passed += 1;
}
if (!src.includes("episodeMeta = null")) {
  failures.push('episodeMeta not reset on run change / error');
} else {
  passed += 1;
}

if (failures.length) {
  console.error('FAILED:\n  ' + failures.join('\n  '));
  process.exit(1);
}
console.log(`isEpisodeResponseStale + truncated-visibility: ${passed} checks passed`);
