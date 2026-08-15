# PR#1 — Position Episode Analytics 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在回测 UI 新增 Episode Analytics 只读诊断：从 executions + predictions + raw 日线派生持仓片段（episode）的入场/离场/收益/MFE/MAE/退出规则统计。

**Architecture:** 新增纯函数模块 `qsys/research_ui/behavior.py`（`derive_episodes` + `summarize_episodes`，零框架依赖、全部可单测）；`ResearchCockpitRepository` 新增 `get_behavior_episodes(run_id)` 装配层（读 executions / predictions.parquet / raw daily store）；`api.py` 新增 `/api/backtest-runs/{run_id}/behavior/episodes`；前端在 view-backtest 末尾新增 Episode Analytics 面板（3 图 + 2 表 + drill-down）。

**Tech Stack:** Python 3.11 / pandas / FastAPI / vanilla JS + Plotly。

**价格口径（已核实，重要）:** canonical manifest `use_adjusted_price=false`，executions `deal_price` = **raw 价格**（如 300487.SZ 2021-01-04 deal 44.6947 ≈ raw open 44.65）。MFE/MAE、post_exit_return 一律用 **raw open/high/low/close**，与 avg_cost 同口径。不得混用前复权价。

---

## File Structure

- Create `qsys/research_ui/behavior.py` — 纯函数 episode 派生 + 聚合引擎。
- Modify `qsys/research_ui/assembler.py` — 新增 `get_behavior_episodes`（数据装配 + 404 语义）。
- Modify `qsys/research_ui/api.py` — 新增 `/behavior/episodes` 路由。
- Modify `qsys/research_ui/web/index.html` — Episode Analytics 面板容器。
- Modify `qsys/research_ui/web/app.js` — fetch + 渲染（直方图/散点/表格/drill-down）。
- Create `tests/test_research_ui_behavior.py` — 纯函数单测。
- Modify `tests/test_research_ui_api.py` — API 契约测试（mocked 200 + real 404）。

设计文档：`docs/requirements/2026-08-15-strategy-behavior-diagnostics-design.md`（已提交）。

---

## Common Conventions（Task 1 引擎必须严格遵守）

1. **排序键**：`(trade_date, sequence)`；仅处理 `status=="filled"`（或缺失）的行；`filled_qty>0`。
2. **持仓状态机**（与既有 `_derive_positions_from_executions` 一致）：buy → `buy_cost += qty*price+fee`, `buy_qty += qty`；sell → `buy_qty -= min(qty, buy_qty)`, `buy_cost -= avg_cost*sold`, `avg_cost = buy_cost/buy_qty`（buy_qty 为 0 时 avg_cost=0）。
3. **episode 边界**：qty `0→>0` 开仓；qty 仍 >0 的 buy 并入；qty 仍 >0 的 sell 留在本 episode；qty `→0` 清仓并结束；之后买入 = 新 episode。
4. **excursion 日**：当天成交应用**后**、`qty>0` 且 `avg_cost>0` 的日。MFE/MAE 用该日 raw high/low 对当日 post-fill avg_cost。
5. **holding_days**：`cal_index[exit] - cal_index[entry] + 1`，calendar = 全部 execution trade_date 去重排序（全局交易日历）。open 用最后持仓 execution 日期作 exit_date。
6. **realized_return（closed）**：cash-weighted `Σsells_proceeds/Σbuys_cost − 1`（sells：`price*qty − fee`；buys：`price*qty + fee`）。
7. **unrealized_return（open）**：`last_close/avg_cost_final − 1`。
8. **exit_reason**：清仓 sell 的 `trade_reason`；未清仓 → `"open"`。
9. **max_drawdown_from_peak**：excursion 日内 close 相对 peak_close 的最大回撤；close 点数 <2 → `0.0`。
10. **score_delta_5d/20d**：`exit_score − score(calendar[i−5/20])`，越界或该日无 score → `None`。
11. **post_exit_return_20d/60d**：symbol 价格序列中 exit_date 之后第 20/60 个交易日 close 相对 exit close 的收益；越界 → `None`。

---

## Task 1: behavior.py — 纯函数 episode 引擎

**Files:**
- Create: `qsys/research_ui/behavior.py`
- Test: `tests/test_research_ui_behavior.py`

- [ ] **Step 1: 写失败测试 — 基础 round-trip（buy→sell）**

```python
# tests/test_research_ui_behavior.py
from __future__ import annotations

import pandas as pd
import pytest

from qsys.research_ui.behavior import derive_episodes, summarize_episodes


def _row(execution_id, date, seq, symbol, side, reason, qty, price, fee=0.0, status="filled"):
    return {
        "execution_id": execution_id, "trade_date": date, "sequence": seq,
        "instrument": symbol, "side": side, "trade_reason": reason,
        "filled_qty": qty, "deal_price": price, "total_fee": fee, "status": status,
    }


def _prices(dates):
    """dates: list of (date, open, high, low, close)."""
    return pd.DataFrame(
        [{"trade_date": d, "open": o, "high": h, "low": l, "close": c}
         for d, o, h, l, c in dates]
    )


def test_episode_simple_round_trip():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0, fee=1.0),
        _row("s1", "2021-02-01", 0, "600000.SH", "sell", "hard_stop", 100, 12.0, fee=1.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-05", 10.3, 11.0, 10.0, 10.8),
        ("2021-02-01", 11.9, 12.5, 11.5, 12.0),
        ("2021-02-02", 12.0, 12.8, 11.8, 12.5),
    ])}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["symbol"] == "600000.SH"
    assert ep["entry_date"] == "2021-01-04"
    assert ep["exit_date"] == "2021-02-01"
    assert ep["exit_reason"] == "hard_stop"
    assert ep["holding_days"] == 2  # 01-04, 02-01
    assert ep["realized_return"] == pytest.approx((1200 - 1) / (1000 + 1) - 1)
    assert ep["unrealized_return"] is None
    # excursion days: 01-04 (after buy), 01-05; 02-01 closes → not counted.
    avg_cost = (1000 + 1) / 100
    assert ep["MFE"] == pytest.approx(max(10.5 / avg_cost - 1, 11.0 / avg_cost - 1))
    assert ep["MAE"] == pytest.approx(min(9.5 / avg_cost - 1, 10.0 / avg_cost - 1))
    # post_exit: exit=02-01, +1=02-02 (day after last price) → 20/60 both null
    assert ep["post_exit_return_20d"] is None
    assert ep["post_exit_return_60d"] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/liuming/.openclaw/workspace/SysQ && /home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python -m pytest tests/test_research_ui_behavior.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qsys.research_ui.behavior'`

- [ ] **Step 3: 实现 `derive_episodes`（含 helper）**

```python
# qsys/research_ui/behavior.py
from __future__ import annotations

from typing import Any

import pandas as pd


def _norm_date(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 10:
        return text[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _is_filled(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "filled").lower() == "filled"


def _price_lookup(frame: pd.DataFrame | None) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Normalize a raw daily frame into {date: {high, low, close}} + sorted dates."""
    if frame is None or frame.empty or "trade_date" not in frame.columns:
        return {}, []
    f = frame.copy()
    if "close" not in f.columns:
        return {}, []
    f["d"] = f["trade_date"].map(_norm_date)
    f = f[f["d"] != ""]
    if f.empty:
        return {}, []
    f = f.sort_values("d")
    rows: dict[str, dict[str, float]] = {}
    for _, r in f.iterrows():
        d = str(r["d"])
        rows[d] = {
            "high": float(r["high"]) if "high" in r.index and pd.notna(r["high"]) else None,
            "low": float(r["low"]) if "low" in r.index and pd.notna(r["low"]) else None,
            "close": float(r["close"]),
        }
    return rows, sorted(rows.keys())


def derive_episodes(
    executions_rows: list[dict[str, Any]],
    *,
    prices_by_symbol: dict[str, pd.DataFrame] | None = None,
    scores_frame: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Reconstruct contiguous holding episodes per symbol from exact fills.

    New episode opens when a buy moves qty 0→>0; buys add; non-closing sells
    stay; closing sell (qty →0) ends it; later buy starts a fresh episode.
    All prices are RAW to match executions.  Read-only.
    """
    rows = [r for r in executions_rows if _is_filled(r)]
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: (_norm_date(r.get("trade_date") or r.get("date")), r.get("sequence") or 0))
    calendar = sorted({_norm_date(r.get("trade_date") or r.get("date")) for r in ordered})
    cal_index = {d: i for i, d in enumerate(calendar)}
    prices_by_symbol = prices_by_symbol or {}

    score_map: dict[tuple[str, str], float] = {}
    if scores_frame is not None and not scores_frame.empty:
        sf = scores_frame.copy()
        inst_col = "instrument" if "instrument" in sf.columns else "symbol"
        if "score" not in sf.columns:
            sf = sf.rename(columns={"score_raw": "score"})
        for _, srow in sf.iterrows():
            d = _norm_date(srow.get("trade_date") or srow.get("date"))
            inst = str(srow.get(inst_col) or "")
            if d and inst:
                try:
                    score_map[(d, inst)] = float(srow["score"])
                except (TypeError, ValueError):
                    continue

    def score_on(date: str, symbol: str) -> float | None:
        return score_map.get((date, symbol))

    # Group fills per symbol in date order.
    fills_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for r in ordered:
        symbol = str(r.get("instrument") or r.get("symbol") or "")
        if not symbol:
            continue
        fills_by_symbol.setdefault(symbol, []).append(r)

    episodes: list[dict[str, Any]] = []
    for symbol, fills in fills_by_symbol.items():
        price_rows, price_dates = _price_lookup(prices_by_symbol.get(symbol))
        episodes.extend(
            _simulate_symbol(symbol, fills, price_rows, price_dates, calendar, cal_index, score_on)
        )
    return episodes
```

```python
# qsys/research_ui/behavior.py (continued) — per-symbol simulation
def _simulate_symbol(
    symbol: str,
    fills: list[dict[str, Any]],
    price_rows: dict[str, dict[str, float]],
    price_dates: list[str],
    calendar: list[str],
    cal_index: dict[str, int],
    score_on,
) -> list[dict[str, Any]]:
    qty = 0.0
    buy_qty = 0.0
    buy_cost = 0.0
    avg_cost = 0.0
    ep: dict[str, Any] | None = None
    episodes: list[dict[str, Any]] = []

    # fill_dates must include every execution day; union with price dates so
    # excursion days without fills still update MFE/MAE.
    fill_dates = {_norm_date(r.get("trade_date") or r.get("date")) for r in fills}
    walk = sorted(fill_dates | set(price_rows.keys()))
    day_fills: dict[str, list[dict[str, Any]]] = {}
    for r in fills:
        day_fills.setdefault(_norm_date(r.get("trade_date") or r.get("date")), []).append(r)

    for date in walk:
        for r in day_fills.get(date, []):
            side = str(r.get("side") or "").lower()
            fqty = float(r.get("filled_qty") or 0)
            price = float(r.get("deal_price") or 0)
            fee = float(r.get("total_fee") or 0)
            if fqty <= 0:
                continue
            if side == "buy":
                if qty == 0:
                    ep = _new_episode(symbol, date, score_on(date, symbol))
                buy_cost += fqty * price + fee
                buy_qty += fqty
                qty += fqty
                avg_cost = buy_cost / buy_qty if buy_qty > 0 else 0.0
                ep["buy_cost"] += fqty * price + fee
            else:  # sell
                if ep is None:
                    ep = _new_episode(symbol, date, score_on(date, symbol))
                sold = min(fqty, qty)
                ep["sell_proceeds"] += sold * price - fee
                if buy_qty > 0:
                    removed = min(sold, buy_qty)
                    buy_cost = max(0.0, buy_cost - avg_cost * removed)
                    buy_qty -= removed
                qty = max(0.0, qty - fqty)
                avg_cost = buy_cost / buy_qty if buy_qty > 0 else 0.0
                if qty == 0:
                    _close_episode(ep, date, r, episodes, price_rows, price_dates, calendar, cal_index, score_on)
                    ep = None
        # excursion update (after the day's fills)
        if ep is not None and qty > 0 and avg_cost > 0:
            prow = price_rows.get(date)
            if prow:
                if prow.get("high") is not None:
                    mfe = prow["high"] / avg_cost - 1.0
                    ep["MFE"] = mfe if ep["MFE"] is None else max(ep["MFE"], mfe)
                if prow.get("low") is not None:
                    mae = prow["low"] / avg_cost - 1.0
                    ep["MAE"] = mae if ep["MAE"] is None else min(ep["MAE"], mae)
                close = prow.get("close")
                if close is not None:
                    ep["peak_close"] = close if ep["peak_close"] is None else max(ep["peak_close"], close)
                    if ep["peak_close"]:
                        dd = (ep["peak_close"] - close) / ep["peak_close"]
                        ep["max_drawdown_from_peak"] = max(ep["max_drawdown_from_peak"], dd)

    # finalize open episodes
    if ep is not None and qty > 0:
        _finalize_open(ep, qty, avg_cost, price_rows, cal_index)
        episodes.append(ep)
    return episodes


def _new_episode(symbol: str, entry_date: str, entry_score: float | None) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "entry_date": entry_date,
        "exit_date": None,
        "entry_score": entry_score,
        "exit_score": None,
        "score_delta_5d": None,
        "score_delta_20d": None,
        "realized_return": None,
        "unrealized_return": None,
        "MFE": None,
        "MAE": None,
        "max_drawdown_from_peak": 0.0,
        "exit_reason": "open",
        "post_exit_return_20d": None,
        "post_exit_return_60d": None,
        "buy_cost": 0.0,
        "sell_proceeds": 0.0,
        "peak_close": None,
    }


def _close_episode(
    ep: dict[str, Any],
    exit_date: str,
    row: dict[str, Any],
    episodes: list[dict[str, Any]],
    price_rows: dict[str, dict[str, float]],
    price_dates: list[str],
    calendar: list[str],
    cal_index: dict[str, int],
    score_on,
) -> None:
    symbol = ep["symbol"]
    ep["exit_date"] = exit_date
    ep["exit_reason"] = str(row.get("trade_reason") or "exit")
    ep["exit_score"] = score_on(exit_date, symbol)
    ep["realized_return"] = (ep["sell_proceeds"] / ep["buy_cost"] - 1.0) if ep["buy_cost"] > 0 else None
    ep["unrealized_return"] = None
    if ep["entry_score"] is not None and ep["exit_score"] is not None:
        i = cal_index.get(exit_date)
        if i is not None:
            if i >= 5:
                s5 = score_on(calendar[i - 5], symbol)
                ep["score_delta_5d"] = ep["exit_score"] - s5 if s5 is not None else None
            if i >= 20:
                s20 = score_on(calendar[i - 20], symbol)
                ep["score_delta_20d"] = ep["exit_score"] - s20 if s20 is not None else None
    ep["post_exit_return_20d"], ep["post_exit_return_60d"] = _post_exit_returns(exit_date, price_rows, price_dates)
    ep.pop("buy_cost", None)
    ep.pop("sell_proceeds", None)
    ep.pop("peak_close", None)
    episodes.append(ep)
```

> `calendar` 由外层 `derive_episodes` 传入 `_simulate_symbol`，再透传给 `_close_episode`；`score_on` 闭包提供该 symbol 的 score 查询。移除 `calendar_at` / `_score_at` 两个多余 helper。

---

### `_simulate_symbol` 边界加固（合并进 Step 3 实现）

在 `for r in day_fills.get(date, [])` 循环内、读取 `side` 之后，加一行守卫：**无持仓时 sell 直接跳过**（防御异常 executions 里只有 sell 无 buy）：

```python
            if side != "buy" and qty <= 0:
                continue
```


def _finalize_open(
    ep: dict[str, Any],
    qty: float,
    avg_cost: float,
    price_rows: dict[str, dict[str, float]],
    cal_index: dict[str, int],
) -> None:
    symbol = ep["symbol"]
    dates = sorted(price_rows.keys())
    if not dates:
        ep["exit_date"] = ep["entry_date"]
    else:
        ep["exit_date"] = dates[-1]
        last_close = price_rows[dates[-1]].get("close")
        if last_close is not None and avg_cost > 0:
            ep["unrealized_return"] = last_close / avg_cost - 1.0
    ep["exit_score"] = None
    ep["realized_return"] = None
    ep.pop("buy_cost", None)
    ep.pop("sell_proceeds", None)
    ep.pop("peak_close", None)


def _post_exit_returns(
    exit_date: str,
    price_rows: dict[str, dict[str, float]],
    price_dates: list[str],
) -> tuple[float | None, float | None]:
    if exit_date not in price_dates:
        return None, None
    i = price_dates.index(exit_date)
    exit_close = price_rows[exit_date].get("close")
    if exit_close is None or exit_close == 0:
        return None, None
    out: list[float | None] = [None, None]
    for j, horizon in enumerate((20, 60)):
        k = i + horizon
        if k < len(price_dates):
            c = price_rows[price_dates[k]].get("close")
            if c is not None and c != 0:
                out[j] = c / exit_close - 1.0
    return out[0], out[1]


def calendar_at(cal: list[str], idx: int) -> str:
    return cal[idx]


def summarize_episodes(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate episodes into a UI-friendly summary."""
    closed = [e for e in episodes if e.get("exit_reason") != "open"]
    returns = [e["realized_return"] for e in closed if e.get("realized_return") is not None]
    holding = [e["holding_days"] for e in episodes if e.get("holding_days") is not None]
    by_reason: dict[str, list[dict[str, Any]]] = {}
    for e in closed:
        by_reason.setdefault(str(e["exit_reason"]), []).append(e)
    return {
        "total_episodes": len(episodes),
        "closed_episodes": len(closed),
        "open_episodes": len(episodes) - len(closed),
        "win_rate": (sum(1 for r in returns if r > 0) / len(returns)) if returns else None,
        "avg_return": (sum(returns) / len(returns)) if returns else None,
        "median_return": sorted(returns)[len(returns) // 2] if returns else None,
        "avg_holding_days": (sum(holding) / len(holding)) if holding else None,
        "by_exit_reason": [
            {
                "exit_reason": reason,
                "count": len(items),
                "win_rate": (sum(1 for e in items if (e.get("realized_return") or 0) > 0) / len(items)) if items else None,
                "avg_return": (sum(e["realized_return"] or 0 for e in items) / len(items)) if items else None,
                "median_return": sorted((e["realized_return"] or 0) for e in items)[len(items) // 2] if items else None,
                "avg_mfe": (sum(e.get("MFE") or 0 for e in items) / len(items)) if items else None,
                "avg_mae": (sum(e.get("MAE") or 0 for e in items) / len(items)) if items else None,
            }
            for reason, items in sorted(by_reason.items())
        ],
    }
```

> 注：`holding_days` 需要在 `_close_episode` / `_finalize_open` 里用 `cal_index` 填入。见 Step 4 的修正——把 `holding_days` 计算并入 episode 收尾（Task 1 收尾统一处理）。

- [ ] **Step 4: 修正 holding_days 并跑绿**

在 `_close_episode` 与 `_finalize_open` 内、`episodes.append` 前，加入：

```python
    entry_i = cal_index.get(ep["entry_date"])
    exit_i = cal_index.get(exit_date)
    ep["holding_days"] = (exit_i - entry_i + 1) if (entry_i is not None and exit_i is not None) else None
```

`_finalize_open` 同理（exit_date 已定为最后价格日或 entry_date，取其 cal_index）。

- [ ] **Step 5: 补全生命周期测试（加仓/减仓/清仓/重建仓）**

```python
def test_episode_add_then_full_exit():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0, fee=1.0),
        _row("b2", "2021-01-08", 0, "600000.SH", "buy", "top_n_entry", 50, 11.0, fee=0.5),
        _row("s1", "2021-01-12", 0, "600000.SH", "sell", "score_delta_exit", 150, 12.0, fee=1.5),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 11.0, 11.5, 10.5, 11.2),
        ("2021-01-12", 12.0, 12.5, 11.5, 12.0),
        ("2021-01-13", 12.0, 12.8, 11.8, 12.5),
        ("2021-01-14", 12.5, 13.0, 12.2, 12.9),
    ])}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["exit_reason"] == "score_delta_exit"
    buys_cost = (100 * 10 + 1) + (50 * 11 + 0.5)
    sells_proc = 150 * 12 - 1.5
    assert ep["realized_return"] == pytest.approx(sells_proc / buys_cost - 1)
    # one episode, not two
    assert ep["symbol"] == "600000.SH"


def test_episode_partial_sell_keeps_episode_open():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0, fee=1.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "score_delta_exit", 40, 11.0, fee=0.4),
        _row("s2", "2021-01-12", 0, "600000.SH", "sell", "hard_stop", 60, 9.0, fee=0.6),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 11.0, 11.5, 10.5, 11.2),
        ("2021-01-12", 9.0, 9.5, 8.5, 9.0),
    ])}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["exit_reason"] == "hard_stop"  # closing sell wins
    assert ep["holding_days"] == 3
    sells_proc = (40 * 11 - 0.4) + (60 * 9 - 0.6)
    buys_cost = 100 * 10 + 1
    assert ep["realized_return"] == pytest.approx(sells_proc / buys_cost - 1)


def test_episode_sell_then_rebuy_starts_new_episode():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "winner_trailing", 100, 12.0),
        _row("b2", "2021-01-12", 0, "600000.SH", "buy", "top_n_entry", 100, 11.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 12.0, 12.5, 11.5, 12.0),
        ("2021-01-12", 11.0, 11.5, 10.5, 11.2),
        ("2021-01-13", 11.0, 11.5, 10.5, 11.2),
    ])}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    assert len(episodes) == 2
    assert episodes[0]["exit_reason"] == "winner_trailing"
    assert episodes[0]["exit_date"] == "2021-01-08"
    assert episodes[1]["entry_date"] == "2021-01-12"
    assert episodes[1]["exit_reason"] == "open"  # second still held
```

- [ ] **Step 6: MFE/MAE / open / score / post_exit 测试**

```python
def test_episode_open_at_data_end_has_unrealized_and_open_reason():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0, fee=1.0),
        _row("b2", "2021-01-08", 0, "600000.SH", "buy", "top_n_entry", 50, 11.0, fee=0.5),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 11.0, 11.5, 10.5, 11.2),
        ("2021-01-09", 11.2, 11.8, 10.8, 11.5),
    ])}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["exit_reason"] == "open"
    assert ep["exit_date"] == "2021-01-09"
    avg_cost = ((100 * 10 + 1) + (50 * 11 + 0.5)) / 150
    assert ep["unrealized_return"] == pytest.approx(11.5 / avg_cost - 1)
    assert ep["realized_return"] is None


def test_episode_scores_and_score_delta():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-02-01", 0, "600000.SH", "sell", "score_delta_exit", 100, 12.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-02-01", 12.0, 12.5, 11.5, 12.0),
        ("2021-02-02", 12.0, 12.5, 11.5, 12.0),
        ("2021-02-03", 12.0, 12.5, 11.5, 12.0),
        ("2021-02-04", 12.0, 12.5, 11.5, 12.0),
        ("2021-02-05", 12.0, 12.5, 11.5, 12.0),
    ])}
    scores = pd.DataFrame({
        "trade_date": ["2021-01-04", "2021-02-01", "2021-01-05", "2021-02-01"],
        "instrument": ["600000.SH"] * 4,
        "score": [0.5, 0.2, 0.1, 0.3],
    })
    episodes = derive_episodes(rows, prices_by_symbol=prices, scores_frame=scores)
    ep = episodes[0]
    assert ep["entry_score"] == pytest.approx(0.5)
    # duplicate (2021-02-01, 600000.SH) → last one wins in dict build: 0.3
    assert ep["exit_score"] == pytest.approx(0.3)
    assert ep["score_delta_20d"] is None  # calendar only has 2 distinct days
    assert ep["score_delta_5d"] is None
```

> 注：上例 scores 表里 (2021-02-01, 600000.SH) 出现两次 → dict 覆盖，取 0.3。测试断言与之对应。calendar 只有 [01-04, 02-01] 两天，score_delta 越界 → None。可另加一例：把 calendar 拉长到 ≥21 个交易日，断言 5d/20d 有值。

```python
def test_episode_post_exit_returns_when_price_data_sufficient():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "winner_trailing", 100, 12.0),
    ]
    # 21 price days after exit (01-08) → 20d post-exit computable, 60d null
    import datetime
    start = datetime.date(2021, 1, 4)
    dates = []
    for i in range(30):
        d = start + datetime.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        dates.append((d.isoformat(), 10.0, 10.5, 9.5, 10.0 + i * 0.01))
    prices = {"600000.SH": _prices(dates)}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    ep = episodes[0]
    assert ep["post_exit_return_20d"] is not None
    assert ep["post_exit_return_60d"] is None
```

- [ ] **Step 7: summarize_episodes 测试**

```python
def test_summarize_episodes_groups_by_exit_reason():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "hard_stop", 100, 12.0),
        _row("b2", "2021-01-04", 0, "600001.SH", "buy", "top_n_entry", 100, 20.0),
        _row("s2", "2021-01-08", 0, "600001.SH", "sell", "hard_stop", 100, 18.0),
        _row("b3", "2021-01-04", 0, "600002.SH", "buy", "top_n_entry", 100, 30.0),
    ]
    prices = {"600000.SH": _prices([("2021-01-04", 10, 10.5, 9.5, 10.2), ("2021-01-08", 12, 12.5, 11.5, 12)]),
              "600001.SH": _prices([("2021-01-04", 20, 20.5, 19.5, 20.2), ("2021-01-08", 18, 18.5, 17.5, 18)]),
              "600002.SH": _prices([("2021-01-04", 30, 30.5, 29.5, 30.2), ("2021-01-08", 30, 30.5, 29.5, 30.2)])}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    summary = summarize_episodes(episodes)
    assert summary["total_episodes"] == 3
    assert summary["closed_episodes"] == 2
    assert summary["open_episodes"] == 1
    assert summary["win_rate"] == pytest.approx(0.5)
    reasons = {r["exit_reason"]: r for r in summary["by_exit_reason"]}
    assert set(reasons) == {"hard_stop"}
    assert reasons["hard_stop"]["count"] == 2
```

- [ ] **Step 8: 全部单测跑绿**

Run: `cd /home/liuming/.openclaw/workspace/SysQ && /home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python -m pytest tests/test_research_ui_behavior.py -v`
Expected: 8 tests PASS.

- [ ] **Step 9: 提交**

```bash
cd /home/liuming/.openclaw/workspace/SysQ
git add qsys/research_ui/behavior.py tests/test_research_ui_behavior.py
git commit -m "feat(ui): episode analytics pure-function engine (behavior.py)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: assembler 装配层 `get_behavior_episodes`

**Files:**
- Modify: `qsys/research_ui/assembler.py`（在 `get_backtest_positions` 之后新增方法；模块顶部 import `derive_episodes, summarize_episodes`）
- Test: `tests/test_research_ui_canonical_backtests.py` 新增用例

- [ ] **Step 1: 写失败测试（fixture 扩展 + 装配层断言）**

在 `tests/test_research_ui_canonical_backtests.py` 的 `_write_canonical_backtest_with_executions` fixture 基础上新增一个写 `predictions.parquet` 与 raw daily 的 fixture（或复用 tmp_path + monkeypatch store）。新增用例：

```python
def test_behavior_episodes_assembler_layer(tmp_path: Path) -> None:
    run_id = _write_canonical_backtest_with_executions(tmp_path)
    repo = ResearchCockpitRepository(project_root=tmp_path)
    with patch.object(repo.store, "load_daily", return_value=_raw_daily_frame()):
        payload = repo.get_behavior_episodes(run_id)
    assert payload["summary"]["total_episodes"] == 2  # 600000 closed, 600001 open
    by_symbol = {e["symbol"]: e for e in payload["episodes"]}
    assert by_symbol["600000.SH"]["exit_reason"] == "score_delta_exit"
    assert by_symbol["600001.SH"]["exit_reason"] == "open"


def test_behavior_episodes_404_unknown_run(tmp_path: Path) -> None:
    repo = ResearchCockpitRepository(project_root=tmp_path)
    with pytest.raises(FileNotFoundError, match="Unknown backtest run_id"):
        repo.get_behavior_episodes("canonical__zzz__zzz")
```

> 注意：fixture `_write_canonical_backtest_with_executions` 里 600000.SH 是 buy 100@10.1 → sell 100@30.3；600001.SH 只 buy 不卖 → open。`_raw_daily_frame()` 提供 3 个交易日的 OHLC（2024-01-02..04），与 executions 日期不重叠 → price_lookup 会按 execution 日期走，MFE/MAE 可能为 None，但这不影响 episode 结构断言。

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/liuming/.openclaw/workspace/SysQ && /home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python -m pytest tests/test_research_ui_canonical_backtests.py -k behavior -v`
Expected: FAIL — `AttributeError: 'ResearchCockpitRepository' object has no attribute 'get_behavior_episodes'`

- [ ] **Step 3: 实现 `get_behavior_episodes`**

```python
# qsys/research_ui/assembler.py — 模块顶部新增 import
from qsys.research_ui.behavior import derive_episodes, summarize_episodes
```

```python
# qsys/research_ui/assembler.py — 方法（放在 get_backtest_positions 之后）
    def get_behavior_episodes(self, run_id: str, *, limit: int = 5000) -> dict[str, Any]:
        """Derive holding-episode diagnostics for a canonical backtest run.

        Reads the immutable executions artifact, the signal predictions parquet
        (for entry/exit scores) and raw daily bars (for MFE/MAE and post-exit
        returns).  All prices are RAW to match execution deal prices.
        """
        source = self._get_canonical_backtest_source(run_id)
        if source is None:
            self._resolve_backtest_report(run_id)  # raises for unknown runs
            return {"episodes": [], "summary": summarize_episodes([])}
        rows = self._read_canonical_executions(source)
        if not rows:
            return {"episodes": [], "summary": summarize_episodes([])}

        manifest = source["manifest"]
        signal_id = str(manifest.get("signal_id") or "")
        signal_run_id = str(manifest.get("signal_run_id") or "")
        scores_frame: pd.DataFrame | None = None
        if signal_id and signal_run_id:
            pred_path = self.project_root / "data" / "research" / "signals" / signal_id / signal_run_id / "predictions.parquet"
            if pred_path.exists():
                try:
                    scores_frame = pd.read_parquet(pred_path)
                except Exception:
                    scores_frame = None

        symbols = sorted({str(r.get("instrument") or r.get("symbol") or "") for r in rows if r.get("instrument") or r.get("symbol")})
        prices_by_symbol: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            df = self.store.load_daily(symbol)
            if df is not None and not df.empty:
                prices_by_symbol[symbol] = df

        episodes = derive_episodes(rows, prices_by_symbol=prices_by_symbol, scores_frame=scores_frame)
        episodes = episodes[:limit]
        return {"episodes": episodes, "summary": summarize_episodes(episodes)}
```

- [ ] **Step 4: 跑绿 + 提交**

Run: `cd /home/liuming/.openclaw/workspace/SysQ && /home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python -m pytest tests/test_research_ui_canonical_backtests.py -k behavior -v`
Expected: 2 tests PASS.

```bash
git add qsys/research_ui/assembler.py tests/test_research_ui_canonical_backtests.py
git commit -m "feat(ui): assembler get_behavior_episodes with 404 semantics

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: api.py `/behavior/episodes` 路由

**Files:**
- Modify: `qsys/research_ui/api.py`（`/positions` 路由之后新增）
- Test: `tests/test_research_ui_api.py`

- [ ] **Step 1: 写失败测试（mocked 200 + real 404）**

```python
# tests/test_research_ui_api.py
def test_behavior_episodes_endpoint_contract(self):
    sample = {"episodes": [{"symbol": "600000.SH", "exit_reason": "hard_stop"}], "summary": {"total_episodes": 1}}
    with patch.object(ResearchCockpitRepository, "get_behavior_episodes", return_value=sample) as mocked:
        response = self.client.get('/api/backtest-runs/some-run/behavior/episodes')
    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertEqual(payload['api_version'], 'v1')
    self.assertEqual(payload['meta']['resource'], 'behavior_episodes')
    self.assertEqual(payload['run_id'], 'some-run')
    self.assertEqual(payload['data']['summary']['total_episodes'], 1)
    mocked.assert_called_once()

def test_behavior_episodes_endpoint_404_when_run_unknown(self):
    response = self.client.get('/api/backtest-runs/canonical__zzz_nonexistent__zzz/behavior/episodes')
    self.assertEqual(response.status_code, 404)
    self.assertIn('Unknown backtest run_id', response.json()['detail'])
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/liuming/.openclaw/workspace/SysQ && /home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python -m pytest tests/test_research_ui_api.py -k behavior -v`
Expected: FAIL — 404 test 因无 `/behavior/episodes` 路由返回 404 之外的 status（FastAPI 默认 404 文案不同，`Unknown backtest run_id` 断言失败）。

- [ ] **Step 3: 实现路由**

```python
# qsys/research_ui/api.py — /positions 之后
    @app.get("/api/backtest-runs/{run_id}/behavior/episodes")
    def get_behavior_episodes(
        run_id: str,
    ) -> dict:
        repo = _fresh_repo_backtests()
        try:
            payload = repo.get_behavior_episodes(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _envelope(
            data=payload,
            meta={"resource": "behavior_episodes", "run_id": run_id},
            run_id=run_id,
        )
```

- [ ] **Step 4: 跑绿 + 提交**

Run: `cd /home/liuming/.openclaw/workspace/SysQ && /home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python -m pytest tests/test_research_ui_api.py -k behavior -v`
Expected: 2 tests PASS.

```bash
git add qsys/research_ui/api.py tests/test_research_ui_api.py
git commit -m "feat(ui): /behavior/episodes API endpoint

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: 前端 — index.html 面板容器

**Files:**
- Modify: `qsys/research_ui/web/index.html`（在 `panel-context-detail` section 之前插入新 panel）

- [ ] **Step 1: 插入 Episode Analytics panel**

在 view-backtest 的 `backtest-main` 内、`panel-context-detail` section 之前插入：

```html
          <section class="panel">
            <div class="panel-head compact-head">
              <div>
                <p class="eyebrow">Strategy Behavior Diagnostics</p>
                <h3>Episode Analytics</h3>
              </div>
              <span id="episode-summary-badge" class="panel-tag"></span>
            </div>
            <div class="subpanel-grid">
              <div>
                <div class="section-label">Episode Return Distribution</div>
                <div id="episode-return-dist-chart" class="chart plotly-chart chart-mid"></div>
              </div>
              <div>
                <div class="section-label">Holding Days Distribution</div>
                <div id="episode-holding-dist-chart" class="chart plotly-chart chart-mid"></div>
              </div>
            </div>
            <div class="subpanel-grid">
              <div>
                <div class="section-label">MFE vs MAE</div>
                <div id="episode-mfe-mae-chart" class="chart plotly-chart chart-mid"></div>
              </div>
              <div>
                <div class="section-label">By Exit Reason</div>
                <div id="episode-exit-reason-table" class="table-wrap"></div>
              </div>
            </div>
            <div class="subpanel-grid">
              <div>
                <div class="section-label">Episodes</div>
                <div id="episode-detail-table" class="table-wrap"></div>
              </div>
            </div>
          </section>
```

- [ ] **Step 2: 无前端测试，人工目检 + 提交**

```bash
git add qsys/research_ui/web/index.html
git commit -m "feat(ui): Episode Analytics panel container

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: 前端 — app.js 渲染

**Files:**
- Modify: `qsys/research_ui/web/app.js`

- [ ] **Step 1: 在 `loadBacktest()` 内 fetch behavior episodes**

在 `loadBacktest()` 的 `renderBacktestSections()` 之后、`selectBacktestDate(selectedDate)` 之前插入：

```javascript
    state.backtest.behavior = null;
    loadBehaviorEpisodes(runId);
```

并在 `loadBacktest` 的 `catch` 块中追加空态渲染（可选）：

```javascript
    renderBehaviorEmpty(error.message);
```

- [ ] **Step 2: 实现 `loadBehaviorEpisodes` 与渲染函数**

在 `renderBacktestPositionsTable` 附近新增：

```javascript
async function loadBehaviorEpisodes(runId) {
  let payload = null;
  try {
    payload = await getJson(`/api/backtest-runs/${runId}/behavior/episodes`, { useCache: false });
  } catch (_error) {
    renderBehaviorEmpty('');
    return;
  }
  const data = unwrapData(payload) || { episodes: [], summary: {} };
  state.backtest.behavior = data;
  renderEpisodePanels(data);
}

function renderEpisodePanels(data) {
  const episodes = data.episodes || [];
  const summary = data.summary || {};
  const badge = byId('episode-summary-badge');
  if (badge) {
    badge.textContent = `${summary.total_episodes ?? episodes.length} episodes · ${summary.closed_episodes ?? 0} closed · ${summary.open_episodes ?? 0} open`;
  }
  renderEpisodeReturnDist(episodes);
  renderEpisodeHoldingDist(episodes);
  renderEpisodeMfeMae(episodes);
  renderEpisodeExitReasonTable(summary.by_exit_reason || []);
  renderEpisodeDetailTable(episodes);
}

function renderEpisodeReturnDist(episodes) {
  const returns = (episodes || [])
    .map((e) => e.realized_return)
    .filter((v) => v !== null && v !== undefined && !isNaN(v))
    .map((v) => v * 100);
  if (!returns.length) { renderChartError('episode-return-dist-chart', 'No closed episodes'); return; }
  const layout = plotlyBaseLayout({ title: 'Episode Realized Return (%)', height: 260, yAxisTitle: 'Count' });
  layout.xaxis = { title: { text: 'Return %' } };
  renderPlotlyChart('episode-return-dist-chart', [{
    type: 'histogram', x: returns, nbinsx: 40,
    marker: { color: CHART_COLORS.strategy, line: { color: '#fff', width: 0.5 } },
    hovertemplate: 'Return %{x:.1f}%<br>Count: %{y}<extra></extra>',
  }], layout);
}

function renderEpisodeHoldingDist(episodes) {
  const days = (episodes || [])
    .map((e) => e.holding_days)
    .filter((v) => v !== null && v !== undefined && !isNaN(v));
  if (!days.length) { renderChartError('episode-holding-dist-chart', 'No holding-day data'); return; }
  const layout = plotlyBaseLayout({ title: 'Holding Days', height: 260, yAxisTitle: 'Count' });
  layout.xaxis = { title: { text: 'Trading days' } };
  renderPlotlyChart('episode-holding-dist-chart', [{
    type: 'histogram', x: days, nbinsx: 40,
    marker: { color: CHART_COLORS.accent, line: { color: '#fff', width: 0.5 } },
    hovertemplate: 'Days %{x}<br>Count: %{y}<extra></extra>',
  }], layout);
}

function renderEpisodeMfeMae(episodes) {
  const closed = (episodes || []).filter((e) => e.realized_return !== null && e.realized_return !== undefined);
  const pts = closed.filter((e) => e.MFE !== null && e.MFE !== undefined && e.MAE !== null && e.MAE !== undefined);
  if (!pts.length) { renderChartError('episode-mfe-mae-chart', 'No MFE/MAE points'); return; }
  const colors = pts.map((e) => (e.realized_return >= 0 ? '#2ca02c' : '#d62728'));
  renderPlotlyChart('episode-mfe-mae-chart', [{
    type: 'scatter', mode: 'markers',
    x: pts.map((e) => e.MFE * 100),
    y: pts.map((e) => e.MAE * 100),
    marker: { color: colors, size: 8, opacity: 0.75 },
    text: pts.map((e) => `${e.symbol}<br>ret ${(e.realized_return * 100).toFixed(1)}%`),
    hovertemplate: '%{text}<br>MFE %{x:.1f}%<br>MAE %{y:.1f}%<extra></extra>',
  }], plotlyBaseLayout({ title: 'MFE vs MAE (green=win, red=loss)', height: 260, yAxisTitle: 'MAE %' }));
}

function renderEpisodeExitReasonTable(reasons) {
  renderDataTable('episode-exit-reason-table', reasons, [
    { key: 'exit_reason', label: 'Exit Reason', sortable: true },
    { key: 'count', label: 'Count', sortable: true, render: (r) => formatNumber(r.count, 0) },
    { key: 'win_rate', label: 'Win Rate', sortable: true, render: (r) => formatPercent(r.win_rate) },
    { key: 'avg_return', label: 'Avg Return', sortable: true, render: (r) => formatPercent(r.avg_return) },
    { key: 'median_return', label: 'Median Return', sortable: true, render: (r) => formatPercent(r.median_return) },
    { key: 'avg_mfe', label: 'Avg MFE', sortable: true, render: (r) => formatPercent(r.avg_mfe) },
    { key: 'avg_mae', label: 'Avg MAE', sortable: true, render: (r) => formatPercent(r.avg_mae) },
  ], { tableKey: 'episode-exit-reason-table', emptyMessage: 'No episodes', hideToolbar: true });
}

function renderEpisodeDetailTable(episodes) {
  renderDataTable('episode-detail-table', episodes, [
    { key: 'symbol', label: 'Symbol', sortable: true, render: (r) => renderInstrumentLink(r.symbol, r.entry_date), filterValue: (r) => r.symbol },
    { key: 'entry_date', label: 'Entry', sortable: true, render: (r) => r.entry_date || '-', sortValue: (r) => r.entry_date || '' },
    { key: 'exit_date', label: 'Exit', sortable: true, render: (r) => r.exit_date || '-', sortValue: (r) => r.exit_date || '' },
    { key: 'holding_days', label: 'Days', sortable: true, render: (r) => formatNumber(r.holding_days, 0), sortValue: (r) => toNumber(r.holding_days) },
    { key: 'exit_reason', label: 'Exit Reason', sortable: true, render: (r) => makeBadge(r.exit_reason || '-', r.exit_reason === 'open' ? 'warning' : 'neutral') },
    { key: 'realized_return', label: 'Return', sortable: true, render: (r) => (r.realized_return === null || r.realized_return === undefined ? (r.unrealized_return === null || r.unrealized_return === undefined ? '-' : `open ${formatPercent(r.unrealized_return)}`) : formatPercent(r.realized_return)), sortValue: (r) => toNumber(r.realized_return ?? r.unrealized_return) },
    { key: 'MFE', label: 'MFE', sortable: true, render: (r) => (r.MFE === null || r.MFE === undefined ? '-' : formatPercent(r.MFE)) },
    { key: 'MAE', label: 'MAE', sortable: true, render: (r) => (r.MAE === null || r.MAE === undefined ? '-' : formatPercent(r.MAE)) },
    { key: 'entry_score', label: 'Entry Score', sortable: true, render: (r) => formatValue(r.entry_score) },
    { key: 'exit_score', label: 'Exit Score', sortable: true, render: (r) => formatValue(r.exit_score) },
    { key: 'score_delta_20d', label: 'ΔScore 20d', sortable: true, render: (r) => formatValue(r.score_delta_20d) },
    { key: 'post_exit_return_20d', label: 'Post-Exit 20d', sortable: true, render: (r) => (r.post_exit_return_20d === null || r.post_exit_return_20d === undefined ? '-' : formatPercent(r.post_exit_return_20d)) },
  ], {
    tableKey: 'episode-detail-table',
    emptyMessage: 'No episodes',
    onRowClick: (row) => jumpToCase(row.symbol, row.entry_date),
  });
}

function renderBehaviorEmpty(message) {
  if (message) {
    renderChartError('episode-return-dist-chart', message);
    renderChartError('episode-holding-dist-chart', message);
    renderChartError('episode-mfe-mae-chart', message);
  }
  byId('episode-exit-reason-table').innerHTML = '<div class="empty">No episodes</div>';
  byId('episode-detail-table').innerHTML = '<div class="empty">No episodes</div>';
}
```

- [ ] **Step 3: 目检无前端测试 + 提交**

```bash
git add qsys/research_ui/web/app.js
git commit -m "feat(ui): render Episode Analytics panel

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: 全量校验 + 收尾

- [ ] **Step 1: 跑全部 UI 相关测试**

Run: `cd /home/liuming/.openclaw/workspace/SysQ && /home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python -m pytest tests/test_research_ui_api.py tests/test_research_ui_canonical_backtests.py tests/test_research_ui_behavior.py -v`
Expected: all PASS（≥ 30 tests）。

- [ ] **Step 2: 跑 harness 相关检查（只读域）**

Run: `cd /home/liuming/.openclaw/workspace/SysQ && /home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python harness/checks/check_usecase_registry.py`（若存在且与本改动相关）
Expected: PASS / no new violations（UI 只读改动不触碰 forbidden paths）。

- [ ] **Step 3: 真实数据冒烟（本地起 API 查一个 posterior run）**

Run:
```bash
cd /home/liuming/.openclaw/workspace/SysQ
/home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python -c "
from qsys.research_ui.assembler import ResearchCockpitRepository
repo = ResearchCockpitRepository(project_root='.')
p = repo.get_behavior_episodes('canonical__posterior_confirmed_top5_financial_rc_50_50_v1__financial_rc_60d_180d_50_50__daily_zscore__blend__007a93600f45de00__3d695c20__bt_2021-01-04_2026-07-31_3d695c20')
print('episodes:', len(p['episodes']), 'summary:', p['summary'])
print('sample:', p['episodes'][0] if p['episodes'] else None)
"
```
> 先确认该 run_id 的真实格式（`list_backtest_runs` 里取一个 posterior run 的 run_id）。

Expected: 输出合理的 episode 数与 summary，样本 episode 字段齐全（含 exit_reason、MFE/MAE、score_delta 等）。

- [ ] **Step 4: 提交收尾（含 spec/plan 已在前面提交）**

```bash
git log --oneline -8
git status --short
```

---

## Task 7: 对抗性验证（Workflow）+ 开 PR

- [ ] **Step 1: 用 Workflow 跑对抗性 review**

在分支上对 PR diff 做独立 adversarial review（错误注入 / 未来函数 / 口径不一致 / 边界），重点：
- executions `trade_date` 8 位格式 vs `YYYY-MM-DD`（`_norm_date` 处理）。
- `filled_qty` / `deal_price` / `total_fee` 缺失或 0 的处理。
- 同日多笔 buy+sell 同 symbol（sequence 顺序）。
- score_map 重复键覆盖语义。
- MFE/MAE 的 avg_cost 基准（post-fill）是否符合约定。
- 不触碰 forbidden paths。

- [ ] **Step 2: 开 PR（不 merge）**

```bash
cd /home/liuming/.openclaw/workspace/SysQ
git add -A
git commit -m "docs(ui): sync UC_UI_ANALYSIS scope with behavior diagnostics"  # 若有文档同步
git push -u origin feat/ui-behavior-episode-analytics
gh pr create --title "feat(ui): Episode Analytics (Strategy Behavior Diagnostics PR#1)" --body "..."
```
PR body 含 Summary / Detected UC / Selected skill / Files changed / Checks run / Remaining TODO。返回 PR URL。

---

## 明确不做（YAGNI）

- 不做 Swap Analytics、Rule Ablation（PR#2/#3）。
- 不重跑 backtest 引擎；不修改 `qsys/backtest/`、ledger、trader、broker、deploy。
- 不做实时增量刷新，每次请求实时计算。
- 不做 Winner/Loser Lifecycle、Alpha/Beta Attribution（延后）。
