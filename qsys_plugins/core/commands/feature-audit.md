---
description: Audit a feature set before training or promotion
argument-hint: "[feature_set] [date range] [universe]"
---

Use `feature-readiness-audit` to judge whether a feature set is ready for training.

Preferred underlying entrypoints:
- existing data health and feature coverage logic
- future thin adapter script for a unified audit command

Required output:
- feature set identity
- coverage summary
- missingness summary
- critical anomalies
- ready decision: `ready`, `warning`, or `blocked`
- recommended next action
