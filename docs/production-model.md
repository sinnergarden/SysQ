# Production Model Management

## Overview

P0.1 introduces a production model manifest mechanism to ensure daily operations use an explicitly approved production model rather than implicitly relying on "latest model" directory.

## Production Manifest

**Location**: `data/models/production_manifest.yaml`

**Structure**:
```yaml
model_path: "data/models/qlib_lgbm"
version: "2025-03-22-v1"
promoted_at: "2025-03-22T00:00:00"
status: "active"
note: "Initial production model - Alpha158 baseline features"
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `model_path` | Yes | Relative or absolute path to the approved model directory |
| `version` | Yes | Version identifier for this manifest |
| `promoted_at` | Yes | ISO timestamp when this was set as production |
| `status` | Yes | `active` or `rollback` |
| `note` | No | Human-readable description |

## Usage

### Daily Operations

The `ModelScheduler.resolve_production_model()` method is now the preferred way to get the production model:

```python
from qsys.live.scheduler import ModelScheduler

# Returns model from manifest, or falls back to latest
model_path = ModelScheduler.resolve_production_model()
```

The daily trading script now uses this by default:
```bash
python scripts/run_daily_trading.py
# or explicitly
python scripts/run_daily_trading.py --model_path data/models/qlib_lgbm
```

### Promoting a New Model

To promote a candidate to production:

1. Update `data/models/production_manifest.yaml`
2. Change `model_path` to point to the new model
3. Update `version` and `note`
4. Update `promoted_at` timestamp

### Rollback

To rollback, simply edit `production_manifest.yaml` and change `model_path` back to a previous version, or change `status` to `rollback`.

## Fallback Behavior

If the manifest is missing or invalid:
1. Log warning
2. Fall back to `find_latest_model()` (most recently modified model directory)
3. If no models found, raise `FileNotFoundError`

This ensures backward compatibility while encouraging manifest adoption.