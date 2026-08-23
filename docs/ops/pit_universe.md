# Point-in-Time Universe Store（csi800_pit_v2 / csi1800_pit_v2）

> PIT universe audit（Stage 8）交付：历史成分股 Point-In-Time 成员查询的 canonical 入口。

## 背景

当前研究 pipeline 的 CSI800 universe 是「今天的中证 800 成分股集合」回溯整个历史
（`STATIC_CURRENT` 语义）。要衡量「真正的 PIT 成分股集合」与当前实现的差异
（constituent survivorship / selection bias），需要一份按时间切片可查询的历史成分股表。

`csi800_pit_v2` 与 `csi1800_pit_v2` 从已冻结的 Tushare `index_weight`
月度快照重建。v2 修复了 v1 的区间结束语义：成分在相邻快照之间持续有效；首次在
下一快照缺席时，`effective_to` 是该快照前一个日历日。v1 把结束日写成最后一次
仍出现的快照日，导致月间人工成员死区，已禁止用于新研究。

CSI800 v1 原始快照概况（v2 复用原始字节，不重新拉取）：
- 235 个月度快照（2007-01-31 … 2026-07-31）
- 2013 只历史出现过成分股，2848 条成员期间
- 每条 span 是闭区间 `[effective_from, effective_to]`，表示该股票是成分股的**连续期间**
- 同一股票可离场再回归（spans 不连续、可能 gap），gap 期间不是成员

## Artifact

位置（`data/` 不入 git）：

```
data/research/universes/csi800_pit_v2/
├── manifest.json                     # 元数据 + provenance hash
├── membership.parquet                # spans 表
└── raw/
    ├── index_weight_snapshots.parquet
    └── snapshot_dates.csv
```

`membership.parquet` schema：

| column | 说明 |
|--------|------|
| `index_code` | `000906.SH` |
| `instrument` | 如 `000002.SZ`（大写） |
| `effective_from` | `YYYYMMDD` 成员开始日（月度快照日） |
| `effective_to` | `YYYYMMDD` 成员末日 |
| `source` | `tushare_index_weight` |
| `source_date` | artifact 构建日 |
| `source_version` | `index_weight_monthly` |

### Provenance 绑定

- `membership_sha256` = sha256( `membership.parquet` **文件字节** )
- `raw_source_hash` = 原始快照表 hash
- `PitUniverseStore` 默认在加载时重算并比对 `membership_sha256`，不匹配即抛错
  （`verify_hash=False` 可绕过，仅供诊断）

## API（`qsys/research/pit_universe.py`）

```python
from qsys.research.pit_universe import PitUniverseStore

store = PitUniverseStore()                     # 默认读 csi800_pit_v2

store.provenance.to_dict()                     # provenance（含 membership_sha256）
store.membership_as_of("2018-06-15")           # 该日成分股（PIT），sorted list
store.is_member("600837.SH", "2018-06-15")     # 单股单日是否成分股
store.membership_window("2018-03-13", "2026-07-31")  # 窗口内 PIT union
store.membership_periods("601991.SH")          # 单股全部成员期间
store.latest_membership()                      # 最近快照成分股（== 静态 800）
store.to_registry_frame(start, end)            # 生成 qlib instrument registry rows
```

### 与 qlib registry 的关系

`to_registry_frame(start, end)` 把窗口内 PIT union 裁剪成每只股票的
`(instrument, start_date, end_date)`，用于构建 qlib 注册表（静态符号集，供 feature 物化）。
**真正的 PIT 过滤在数据构建时按 `membership_as_of(date)` 逐日应用** —— 注册表只决定
物化哪些股票，不决定某日该股票是否可作为训练/预测样本。

## 测试

`tests/ops/test_pit_universe.py`（synthetic fixture + 真实 artifact 集成测试，artifact 缺失时 skip）。

## 可复现构建

构建只读取 v1 artifact 内冻结的 `raw/index_weight_snapshots.parquet`，先在同目录
staging 中生成并验证两个 v2 artifact，全部通过后再原子发布。构建代码与 label
配置必须已提交，scoped worktree 不能有未提交改动。

```bash
python scripts/research/rebuild_pit_universes.py
```

每个 v2 manifest 绑定 `raw_source_hash`、`membership_sha256`、
`registry_sha256`、`builder_code_sha256`、完整 git commit，并记录逐 index 快照
数量、研究窗口逐交易日成员数验证与精确快照集合验证。逻辑 registry 仍使用
`csi800_pit_union` / `csi1800_pit_union`，但其文件 hash 必须等于 v2 manifest，
label manifest 也必须再次绑定该 hash。

月度 `index_weight` 快照只能表达“截至该快照所知”的成员变化；若指数在月中发生
临时调整而数据源没有对应快照，v2 也无法复原该月中生效日。因此本文所称 PIT 是
**月度快照 as-of carry-forward PIT**，不是交易所逐事件级成分历史。

## 日常 CSI1800 数据同步快照

历史研究 artifact `csi1800_pit_v2` 是 hash-bound、不可变的，日常数据同步不得为了
延长当前日期而覆盖它。`scripts/data_sync.py --universe csi1800 --apply` 为目标交易日
解析不晚于该日的最新 CSI800 与 CSI1000 月度快照，并写入独立的 operational artifact：

```
data/research/universes/csi1800_pit_daily/YYYYMMDD/
├── manifest.json
└── membership.parquet
```

目录按目标交易日寻址，不使用 `latest` 指针。同一天重复运行仅在 semantic hash 与
membership 文件 hash 均匹配时复用；成分发生差异、数量不是严格 800 + 1000、两个
指数有重叠或只有晚于目标日的快照时均 fail closed。该快照用于决定当天需要同步的
股票和生成当前 `csi1800.txt` qlib registry，不改变历史 PIT-v2 研究结果。

## Stage 9B：完整 PIT retrain 用法（correctness audit）

Full-PIT retrain 把 PIT 语义同时应用到训练与预测，而不是只过滤 prediction universe
（audit Section 17 禁止「只修 prediction universe 而不修 training」）。

### 三个改动点

1. **qlib 注册表**：`csi800_pit_union`（`data/qlib_bin/instruments/csi800_pit_union.txt`）
   由 `to_registry_frame('2018-01-01', '2026-07-31')` 生成，保留**逐 span** 行（不 collapse），
   qlib 0.9.7 原生支持每符号多行 span；只决定 feature 物化哪些股票。
2. **行级 PIT 过滤**：`LightGBMSingleLabelGenerator(pit_membership=True)`。`generate()`
   在 `_load_data` 之后对共享 frame 按行应用
   `is_member(instrument, feature_date)`，train 与 predict 是同一次过滤（同一 frame 的子集）。
   **过滤读的是 artifact 的 gapped spans，不是注册表的 min/max** —— 离场再回归的股票
   在 gap 期间不会被当成成员。缓存 identity 含 `pit_membership`，PIT 与非 PIT 永不共用
   per-window cache。
3. **PIT label**：`configs/labels/fwd_ret_180d_raw_pit.yaml`（`universe: csi800_pit_union`，
   `label_id_override` 使 store 行 label_id 与配置一致），避免覆盖 baseline 的
   `fwd_ret_180d_raw` store。

### 已知 PIT 边界语义

- 成员资格按 **feature date**（数据日）判定；某月首个执行日的 feature date 是上月末
  交易日，因此该一次调仓日使用上月的成员表（与按执行日过滤的 Stage 6 诊断有一个
  rebalance 的月界差异）。不要在同一 run 混用两种过滤。
- 9B 回测仍需 0 个非成员买入的验证门（Stage 6 的 filter-only 诊断有 ~3% 买入泄漏）。

## 使用约束

- 研究结论引用 PIT 结果时必须绑定 `membership_sha256`（artifact 不入 git，hash 是唯一锚点）。
- 本 store 是只读访问器，不负责数据回填；回填走 `qsys/ops/universe_history.py`。
