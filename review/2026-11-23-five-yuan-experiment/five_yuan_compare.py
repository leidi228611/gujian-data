# -*- coding: utf-8 -*-
"""5元股 +5% 链式实验：选股方式对比（random / trend / volume）。

2026-11-23 决策官要求 A（对比选股） + C（扩大样本）。
回测口径同 exp_five_yuan：单槽位、T+1、收盘买、+5%卖、卖出当天再买。
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"D:\自动炒股")
import sys
sys.path.insert(0, str(ROOT))
from stocklens import tdx  # noqa: E402
import numpy as np  # noqa: E402

CACHE = ROOT / "_data" / "five_yuan_compare.json"
TARGET = 0.05
MAX_PRICE = 5.0
MIN_AMOUNT = 1e7
MODES = ("random", "trend", "volume")


def load_window(start: int, end: int, force: bool = False) -> dict:
    if CACHE.exists() and not force:
        d = json.loads(CACHE.read_text(encoding="utf-8"))
        if d.get("start") == start and d.get("end") == end:
            return d
    t0 = time.time()
    u = tdx.universe()
    bars = {}
    for code in sorted(u.get("stocks") or {}):
        try:
            a = tdx.read_stock(code)
        except Exception:  # noqa: BLE001
            continue
        dates = np.asarray(a["date"], dtype=np.int64)
        close = np.asarray(a["close"], dtype=float)
        high = np.asarray(a["high"], dtype=float)
        amount = np.asarray(a["amount"], dtype=float)
        mask = (dates >= start - 100) & (dates <= end) & (close > 0)
        if not mask.any():
            continue
        bars[code] = {
            "dates": [int(x) for x in dates[mask]],
            "close": [float(x) for x in close[mask]],
            "high": [float(x) for x in high[mask]],
            "amount": [float(x) for x in amount[mask]],
        }
    cal = [int(x) for x in tdx.index_series("sh000001")["date"] if start <= int(x) <= end]
    payload = {"start": start, "end": end, "calendar": cal, "bars": bars,
               "built_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"  cache {len(bars)} stocks, {len(cal)} days, {time.time()-t0:.1f}s")
    return payload


def build_signals(data: dict) -> dict[int, list[dict]]:
    """每天候选：close<=5, amount>=1e7；附 mom5 / ma20 / vol_ratio。"""
    bars = data["bars"]
    signals: dict[int, list[dict]] = defaultdict(list)
    # 每只股票按自身日期序列算指标
    for code, b in bars.items():
        ds = b["dates"]
        closes = b["close"]
        highs = b["high"]
        amts = b["amount"]
        n = len(ds)
        for i in range(n):
            if i < 20:
                continue
            d = ds[i]
            c = closes[i]
            if c <= 0 or c > MAX_PRICE or amts[i] < MIN_AMOUNT:
                continue
            ma20 = sum(closes[i - 19:i + 1]) / 20
            mom5 = c / closes[i - 5] - 1 if closes[i - 5] > 0 else 0
            vol_ratio = amts[i] / (sum(amts[i - 19:i + 1]) / 20) if sum(amts[i - 19:i + 1]) else 0
            signals[d].append({"code": code, "close": c, "high": highs[i],
                               "ma20": ma20, "mom5": mom5, "vol_ratio": vol_ratio,
                               "amount": amts[i]})
    return signals


def pick(cands: list[dict], mode: str, rng: random.Random | None) -> dict:
    if mode == "random":
        return rng.choice(cands) if rng else cands[0]
    if mode == "trend":
        good = [c for c in cands if c["close"] > c["ma20"] and c["mom5"] > 0]
        pool = good or cands
        return max(pool, key=lambda c: c["mom5"])
    # volume
    return max(cands, key=lambda c: c["vol_ratio"])


def run(data: dict, signals: dict, mode: str, seed: int = 0) -> dict:
    cal = data["calendar"]
    bars = data["bars"]
    rng = random.Random(seed) if mode == "random" else None
    pos = None
    trades, opens = [], []
    def bar(code, d):
        b = bars[code]
        try:
            i = b["dates"].index(d)
        except ValueError:
            return None
        return b["close"][i], b["high"][i]
    for d in cal:
        if pos is not None and d > pos["buy_day"]:
            b = bar(pos["code"], d)
            if b and b[1] >= pos["buy_price"] * (1 + TARGET):
                trades.append({"code": pos["code"], "buy_day": pos["buy_day"], "sell_day": d,
                               "hold": cal.index(d) - cal.index(pos["buy_day"]),
                               "ret": TARGET * 100})
                pos = None
        if pos is None:
            cands = signals.get(d) or []
            if cands:
                c = pick(cands, mode, rng)
                pos = {"code": c["code"], "buy_day": d, "buy_price": c["close"]}
    if pos:
        opens.append({"code": pos["code"], "buy_day": pos["buy_day"],
                      "hold": len(cal) - 1 - cal.index(pos["buy_day"])})
    return {"trades": trades, "opens": opens}


def summarize(runs: list[dict]) -> dict:
    closed = [t for r in runs for t in r["trades"]]
    opens = [o for r in runs for o in r["opens"]]
    holds = [t["hold"] for t in closed]
    total = len(holds) + len(opens)
    dist = defaultdict(int)
    for h in holds:
        k = "1" if h <= 1 else "2" if h == 2 else "3" if h == 3 else "4" if h == 4 else "5" if h == 5 else "6-10" if h <= 10 else ">10"
        dist[k] += 1
    return {
        "total_positions": total, "closed": len(holds), "open": len(opens),
        "hit_rate": round(len(holds) / total * 100, 1) if total else None,
        "avg_hold_closed": round(sum(holds) / len(holds), 2) if holds else None,
        "median_hold_closed": statistics.median(holds) if holds else None,
        "avg_open_hold": round(sum(o["hold"] for o in opens) / len(opens), 2) if opens else None,
        "avg_all_hold": round((sum(holds) + sum(o["hold"] for o in opens)) / total, 2) if total else None,
        "within_1d": round(sum(1 for h in holds if h <= 1) / len(holds) * 100, 1) if holds else None,
        "within_3d": round(sum(1 for h in holds if h <= 3) / len(holds) * 100, 1) if holds else None,
        "within_5d": round(sum(1 for h in holds if h <= 5) / len(holds) * 100, 1) if holds else None,
        "dist": dict(sorted(dist.items(), key=lambda x: (x[0].startswith(">"), x[0].startswith("6"), x[0]))),
        "open_holds": [o["hold"] for o in opens],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=20250101)
    ap.add_argument("--end", type=int, default=20261123)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    data = load_window(args.start, args.end, args.force)
    signals = build_signals(data)
    rep = {"start": args.start, "end": args.end, "days": len(data["calendar"]), "modes": {}}
    all_rows = []
    for mode in MODES:
        runs = [run(data, signals, mode, seed) for seed in range(1, (args.seeds if mode == "random" else 1) + 1)]
        rep["modes"][mode] = summarize(runs)
        for seed_i, r in enumerate(runs, 1):
            for t in r["trades"]:
                all_rows.append({"mode": mode, "seed": seed_i, **{k: t[k] for k in ("code", "buy_day", "sell_day", "hold", "ret")},
                                 "status": "closed"})
            for o in r["opens"]:
                all_rows.append({"mode": mode, "seed": seed_i, "code": o["code"], "buy_day": o["buy_day"],
                                 "sell_day": "", "hold": o["hold"], "ret": "", "status": "open"})
        s = rep["modes"][mode]
        print(f"  {mode}: 总开仓 {s['total_positions']} ｜ 已卖 {s['closed']} ｜ 命中 {s['hit_rate']}% ｜ "
              f"已卖均持 {s['avg_hold_closed']} 天 ｜ 未平仓均持 {s['avg_open_hold']} 天 ｜ 含未平仓均持 {s['avg_all_hold']}")
    # 报告
    out = ROOT / "docs" / f"五元股选股对比-{args.end}.md"
    lines = [f"# 5元股 +5% 链式：选股方式对比（{args.start}~{args.end}）", "",
             f"- 交易日 {rep['days']} 天；股票池：收盘≤5元、日成交额≥1000万；单槽位、T+1、收盘买、盘中最高触+5%卖",
             f"- random：{args.seeds} 个种子合并；trend/volume 确定性选股", "",
             "| 选股方式 | 总开仓 | 已卖 | 命中率 | 已卖均持 | 中位 | 未平仓均持 | 含未平仓均持 | ≤3天 | ≤5天 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for mode in MODES:
        s = rep["modes"][mode]
        lines.append(f"| {mode} | {s['total_positions']} | {s['closed']} | {s['hit_rate']}% | {s['avg_hold_closed']} | "
                     f"{s['median_hold_closed']} | {s['avg_open_hold']} | {s['avg_all_hold']} | {s['within_3d']}% | {s['within_5d']}% |")
    lines += ["", "## 分布（已卖出）", ""]
    for mode in MODES:
        lines.append(f"- {mode}: {rep['modes'][mode]['dist']}")
    out.write_text("\n".join(lines), encoding="utf-8")
    import csv
    rows_csv = ROOT / "_data" / f"five_yuan_compare_trades_{args.end}.csv"
    with rows_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["mode", "seed", "code", "buy_day", "sell_day", "hold", "ret", "status"])
        w.writeheader(); w.writerows(all_rows)
    print(f"  ✅ trades {len(all_rows)} -> {rows_csv}")
    print(f"  ✅ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
