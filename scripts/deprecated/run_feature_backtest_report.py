from __future__ import annotations

import sys
from pathlib import Path
import subprocess
import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

EXPERIMENTS = [
    ("baseline", "qlib_lgbm"),
    ("phase1", "qlib_lgbm_phase1"),
    ("phase12", "qlib_lgbm_phase12"),
    ("phase123", "qlib_lgbm_phase123"),
]


def main():
    rows = []
    for label, model_name in EXPERIMENTS:
        model_path = project_root / 'data' / 'models' / model_name
        if not model_path.exists():
            rows.append({"experiment": label, "status": "missing_model", "model_path": str(model_path)})
            continue
        cmd = [
            sys.executable,
            str(project_root / 'scripts' / 'run_backtest.py'),
            '--model_path', str(model_path),
            '--universe', 'csi300',
            '--start', '2026-02-02',
            '--end', '2026-03-20',
            '--top_k', '5',
        ]
        proc = subprocess.run(cmd, cwd=project_root, text=True, capture_output=True)
        rows.append({
            'experiment': label,
            'status': 'ok' if proc.returncode == 0 else 'failed',
            'model_path': str(model_path),
            'returncode': proc.returncode,
            'stdout_tail': (proc.stdout or '')[-1200:],
            'stderr_tail': (proc.stderr or '')[-1200:],
        })
    df = pd.DataFrame(rows)
    out = project_root / 'docs' / 'research' / 'feature_backtest_report.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ['# Feature Backtest Report', '', '## Minimal experiment status', '', df.to_markdown(index=False)]
    out.write_text('\n'.join(lines), encoding='utf-8')
    print(f'written {out}')


if __name__ == '__main__':
    main()
