# -*- coding: utf-8 -*-
"""新研究臂：5 元以内股票「涨 5% 就卖、卖出立刻换下一只」持股天数实验（2026-11-23）。

规则（决策官 2026-11-23 提出）：
  · 每个交易日收盘（14:52≈收盘价）买入一只 **收盘价 ≤ 5 元** 的 A 股；
  · 持有到相对买入价 **涨幅 ≥ 5%** 时卖出；
  · 卖出后**当天立即**买入下一只 ≤5 元股票，继续等 +5%，依次循环；
  · 一次只持一只（单槽位）。

两种成交假设（回测必须说明）：
  · `high`（乐观）：持有期内某日最高价 ≥ 买入价×1.05 → 按 +5.00% 成交；
  · `close`（保守）：持有期内某日收盘价 ≥ 买入价×1.05 → 按当日收盘价成交。
T+1：买入当日不能卖出，最早次日。

用法：
  python -X utf8 -m stocklens.exp_five_yuan --start 20260101 --end 20261123 --seeds 10
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "_data" / "five_yuan_bars.json"
TARGET = 0.05
MAX_PRICE = 5.0


def _load_universe() -> list[str]:
    from . import tdx
    u = tdx.universe()
    return sorted(u.get("stocks") or {})


def _read_all(start: int, end: int, min_amount: float = 1e7) -> dict[str, dict[int, tuple[float, float, float]]]:
    """返回 {code: {date: (close, high, amount)}}，只保留窗口内且有成交的日线。"""
    from . import tdx
    import numpy as np
    out: dict[str, dict[int, tuple[float, float, float]]] = {}
    for code in _load_universe():
        try:
            a = tdx.read_stock(code)
        except Exception:  # noqa: BLE001
            continue
        dates = np.asarray(a["date"], dtype=np.int64)
        close = np.asarray(a["close"], dtype=float)
        high = np.asarray(a["high"], dtype=float)
        amount = np.asarray(a["amount"], dtype=float)
        mask = (dates >= start) & (dates <= end) & (close > 0)
        if not mask.any():
            continue
        d: dict[int, tuple[float, float, float]] = {}
        for dd, c, h, amt in zip(dates[mask], close[mask], high[mask], amount[mask]):
            if amt >= min_amount:
                d[int(dd)] = (float(c), float(h), float(amt))
        if d:
            out[code] = d
    return out


def build_cache(start: int, end: int, force: bool = False) -> dict:
    if CACHE.exists() and not force:
        try:
            d = json.loads(CACHE.read_text(encoding="utf-8"))
            if d.get("start") == start and d.get("end") == end:
                return d
        except Exception:  # noqa: BLE001
            pass
    t0 = time.time()
    bars = _read_all(start, end)
    payload = {"start": start, "end": end, "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "bars": {code: {str(k): v for k, v in days.items()} for code, days in bars.items()}}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"  已缓存 {len(bars)} 只股票 × {end-start} 窗口（{time.time()-t0:.1f}s）-> {CACHE}")
    return payload


def run(bars: dict[str, dict[int, tuple[float, float]]], calendar: list[int],
        sell_mode: str = "high", seed: int = 1) -> dict[str, Any]:
    """执行单槽位链式回测；bars[code][date]=(close, high)（amount 可省）。"""
    rng = random.Random(seed)
    trades: list[dict] = []
    pos: dict | None = None
    for t in calendar:
        # 1) 有仓位：T+1 之后达到目标就卖
        if pos is not None and t > pos["buy_day"]:
            bar = bars.get(pos["code"], {}).get(t)
            if bar:
                close, high = bar[0], bar[1]
                hit = high >= pos["buy_price"] * (1 + TARGET) if sell_mode == "high" else close >= pos["buy_price"] * (1 + TARGET)
                if hit:
                    sell_price = pos["buy_price"] * (1 + TARGET) if sell_mode == "high" else close
                    hold = calendar.index(t) - calendar.index(pos["buy_day"])
                    trades.append({"code": pos["code"], "buy_day": pos["buy_day"], "sell_day": t,
                                   "buy_price": pos["buy_price"], "sell_price": round(sell_price, 3),
                                   "hold_days": hold, "ret_pct": round((sell_price / pos["buy_price"] - 1) * 100, 2)})
                    pos = None
        # 2) 空仓：当天收盘买一只 ≤5 元
        if pos is None:
            cand = [c for c, days in bars.items()
                    if t in days and 0 < days[t][0] <= MAX_PRICE]
            if cand:
                code = rng.choice(cand)
                pos = {"code": code, "buy_day": t, "buy_price": bars[code][t][0]}
    # 期末未平仓
    open_pos = None
    if pos is not None:
        open_pos = {"code": pos["code"], "buy_day": pos["buy_day"],
                    "hold_days": len(calendar) - 1 - calendar.index(pos["buy_day"]),
                    "buy_price": pos["buy_price"]}
    return {"trades": trades, "open": open_pos, "sell_mode": sell_mode, "seed": seed}


def stats(all_trades: list[dict], opens: list[dict]) -> dict[str, Any]:
    holds = [t["hold_days"] for t in all_trades]
    open_holds = [o["hold_days"] for o in opens]
    total = len(holds) + len(open_holds)
    dist: dict[str, int] = {}
    for h in holds:
        k = "1" if h <= 1 else "2" if h == 2 else "3" if h == 3 else "4" if h == 4 else "5" if h == 5 else "6-10" if h <= 10 else ">10"
        dist[k] = dist.get(k, 0) + 1
    n = len(holds)
    d = {
        "trades": n, "open": len(opens),
        "avg_hold": round(sum(holds) / n, 2) if n else None,
        "median_hold": sorted(holds)[n // 2] if n else None,
        "min_hold": min(holds) if n else None,
        "max_hold": max(holds) if n else None,
        "within_1d_pct": round(sum(1 for h in holds if h <= 1) / n * 100, 1) if n else None,
        "within_3d_pct": round(sum(1 for h in holds if h <= 3) / n * 100, 1) if n else None,
        "within_5d_pct": round(sum(1 for h in holds if h <= 5) / n * 100, 1) if n else None,
        "dist": dict(sorted(dist.items(), key=lambda x: (x[0].startswith(">"), x[0].startswith("6"), x[0]))),
        "open_hold_days": open_holds,
        "total_positions": total,
        "hit_rate_pct": round(len(holds) / total * 100, 1) if total else None,
        "avg_all_hold": round((sum(holds) + sum(open_holds)) / total, 2) if total else None,
        "avg_open_hold": round(sum(open_holds) / len(open_holds), 2) if open_holds else None,
    }
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description="5元股 +5% 卖出链式持股天数实验")
    ap.add_argument("--start", type=int, default=20260101)
    ap.add_argument("--end", type=int, default=20261123)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--mode", choices=["high", "close", "both"], default="both")
    ap.add_argument("--force-cache", action="store_true")
    args = ap.parse_args()
    payload = build_cache(args.start, args.end, args.force_cache)
    from . import tdx
    cal = [int(x) for x in tdx.index_series("sh000001")["date"]]
    cal = [d for d in cal if args.start <= d <= args.end]
    bars = {c: {int(d): (float(v[0]), float(v[1])) for d, v in days.items()}
            for c, days in payload["bars"].items()}
    modes = ["high", "close"] if args.mode == "both" else [args.mode]
    report = {"start": args.start, "end": args.end, "calendar_days": len(cal),
              "universe": len(bars), "modes": {}}
    for mode in modes:
        per_seed, all_trades, opens = [], [], []
        for seed in range(1, args.seeds + 1):
            r = run(bars, cal, sell_mode=mode, seed=seed)
            st = stats(r["trades"], [r["open"]])
            per_seed.append(st)
            all_trades.extend(r["trades"])
            if r["open"]:
                opens.append(r["open"])
        pooled = stats(all_trades, opens)
        report["modes"][mode] = {
            "per_seed": [{"avg_hold": s["avg_hold"], "trades": s["trades"],
                          "open": s["open"], "open_hold_days": s["open_hold_days"],
                          "hit_rate_pct": s["hit_rate_pct"]} for s in per_seed],
            "avg_hold_mean": round(sum(s["avg_hold"] for s in per_seed if s["avg_hold"]) / max(1, sum(1 for s in per_seed if s["avg_hold"])), 2) if per_seed else None,
            "pooled": pooled,
        }
        print(f"  [{mode}] 平均持股 {report['modes'][mode]['avg_hold_mean']} 天，成交 {pooled['trades']} 笔，期末持仓 {pooled['open']}")
    out = ROOT / "reports" / f"五元股持股实验-{args.end}.md"
    lines = [f"# 5 元以内 +5% 卖出 · 持股天数实验（{args.start}~{args.end}）", "",
             f"- 有效股票池：{len(bars)} 只（窗口内有成交，且日成交额 ≥1,000 万）",
             f"- 交易日：{len(cal)} 天；每种子起始资金不建模（只看持股天数/成交）",
             ""]
    for mode, m in report["modes"].items():
        p = m["pooled"]
        lines += [f"## 成交假设：{'盘中最高价触 +5%（乐观）' if mode=='high' else '收盘价 ≥ +5%（保守）'}", "",
                  f"- 总开仓 {p['total_positions']} 次：已卖出 {p['trades']} 笔，期末仍持仓 {p['open']} 笔",
                  f"- **+5% 命中率 {p['hit_rate_pct']}%**（已卖出/总开仓）",
                  f"- 已卖出平均持股 **{m['avg_hold_mean']} 个交易日**（合并 {p['avg_hold']}，中位数 {p['median_hold']}）",
                  f"- 未平仓平均已持有 **{p['avg_open_hold']} 个交易日**（明细 {p['open_hold_days']}）",
                  f"- **含未平仓平均持有 {p['avg_all_hold']} 个交易日**",
                  f"- 已卖出分布：{p['dist']}",
                  f"- 已卖出中 ≤1 天 {p['within_1d_pct']}% ｜ ≤3 天 {p['within_3d_pct']}% ｜ ≤5 天 {p['within_5d_pct']}%", ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✅ 报告: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
