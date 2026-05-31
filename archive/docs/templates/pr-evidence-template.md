# PR Evidence Template

```markdown
## Goal

_One paragraph. What does this PR accomplish?_

## Scope

- _Bullet list of what is included_

## Non-goals

- _Bullet list of what is explicitly NOT included_

## Changed Files

| File | Change |
|---|---|
| `path/to/file.py` | _brief summary_ |
| ... | ... |

## Red-Zone Files Touched

_List any files from the red-zone list (see
[agent-operating-contract.md](../contracts/agent-operating-contract.md)).
If none, write "None"._

## Time Semantics Impact

_Describe whether trade_date, data_date, or label window semantics are
affected. If not, write "None"._

## Research Artifact Schema Impact

_Describe whether any artifact schema (signal parquet, label parquet,
manifest JSON) changes. If not, write "None"._

## Trading Behavior Impact

_Describe whether portfolio construction, order generation, execution,
or ledger behavior changes. If not, write "None"._

## Backward Compatibility

- _What existing behavior remains unchanged?_
- _What migrations are needed (if any)?_

## Verification

```bash
# Command 1: unit tests
python -m pytest tests/path/test_file.py -v 2>&1 | tail -20
```

```
(Paste output here)
```

```bash
# Command 2: checker
python scripts/checks/check_signal_schema.py --path /tmp/sample.csv
```

```
(Paste output here)
```

## Generated Evidence Files

- `path/to/evidence/file.json`

## Known Limitations

- _Bullet list_

## Reviewer Checklist

- [ ] Scope matches PR title
- [ ] Non-goals are respected
- [ ] Red-zone files justified (if touched)
- [ ] Time semantics impact clear
- [ ] Tests cover new and existing behavior
- [ ] Verification commands produce expected output
- [ ] Backward compatibility documented
- [ ] Agent operating contract rules followed
```
