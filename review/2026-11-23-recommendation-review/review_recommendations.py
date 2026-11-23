# -*- coding: utf-8 -*-
"""股鉴推荐复盘（2026-11-23）：从结构化推荐记录以来逐票复盘。

数据来源：
  · data/picks_daily.json —— 每日推荐（结构化记录从 2026-10-21 起，33 条）
  · 当前 + 历史备份账本（_backup、_archive_instances）—— 实际买入/卖出
  · 通达信日线（已含 2026-11-23）—— 收盘/最高价

口径：
  · 有实际成交：按真实买入价、卖出价（或最新收盘）算收益；持有天数=交易日数
  · 未成交：以推荐价为基准，跟踪推荐后 5 个交易日，看期间收盘/最高涨幅
  · 「期间最高」分别按收盘价、当日最高价统计
"""
from __future__ import annotations

import csv
import glob
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\自动炒股")
sys.path.insert(0, str(ROOT))

from stocklens import tdx  # noqa: E402

PICKS = ROOT / "data" / "picks_daily.json"
OUT_MD = ROOT / "docs" / "推荐复盘-20261123.md"
OUT_CSV = ROOT / "_data" / "recommendation_review_20261123.csv"
LOOKAHEAD = 5


def load_picks() -> list[dict]:
    picks = json.loads(PICKS.read_text(encoding="utf-8"))
    out, seen = [], set()
    for p in picks:
        key = (str(p.get("date")), str(p.get("code")))
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": str(p["date"]), "code": str(p["code"]),
                    "name": p.get("name") or "", "price": float(p.get("price") or 0)})
    return sorted(out, key=lambda x: (x["date"], x["code"]))


def trade_books() -> tuple[list[dict], list[dict]]:
    files = ["data/sim.json", "data_b/data/sim.json", "data_c/data/sim.json"]
    files += glob.glob(str(ROOT / "_backup" / "**" / "sim.json"), recursive=True)
    files += glob.glob(str(ROOT / "_archive_instances" / "**" / "*sim.json"), recursive=True)
    buys, sells, seen = [], [], set()
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for t in d.get("trades") or []:
            act = str(t.get("action") or "")
            if "买入" not in act and "卖出" not in act:
                continue
            key = (t.get("at"), t.get("code"), str(t.get("shares")), str(t.get("price")), act)
            if key in seen:
                continue
            seen.add(key)
            item = {"at": str(t.get("at") or ""), "code": str(t.get("code") or ""),
                    "name": t.get("name") or "", "shares": float(t.get("shares") or 0),
                    "price": float(t.get("price") or 0), "pnl": t.get("pnl"),
                    "_file": f}
            (buys if "买入" in act else sells).append(item)
    buys.sort(key=lambda x: x["at"])
    sells.sort(key=lambda x: x["at"])
    return buys, sells


def bars_for(code: str) -> dict[int, tuple[float, float]]:
    try:
        a = tdx.read_stock(code)
        return {int(d): (float(c), float(h)) for d, c, h in zip(a["date"], a["close"], a["high"])}
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    picks = load_picks()
    buys, sells = trade_books()
    cal = sorted(int(x) for x in tdx.index_series("sh000001")["date"])
    cal_idx = {d: i for i, d in enumerate(cal)}
    latest = cal[-1]

    # ---------- 实际成交复盘（按事件聚合 A+B 镜像） ----------
    grouped: dict[tuple, list] = {}
    for b in buys:
        key = (b["code"], b["at"][:10], b["price"])
        grouped.setdefault(key, []).append(b)
    buys_events = []
    for key, bs in grouped.items():
        buys_events.append({
            "at": bs[0]["at"], "code": bs[0]["code"], "name": bs[0]["name"],
            "price": bs[0]["price"], "shares": sum(x["shares"] for x in bs),
            "n": len(bs),
        })
    actual_rows = []
    for b in buys_events:
        bd = int(str(b["at"])[:10].replace("-", ""))
        bars = bars_for(b["code"])
        ss = [s for s in sells if s["code"] == b["code"] and s["at"] > b["at"]]
        exit_day, exit_price = None, None
        if ss:
            sh = sum(x["shares"] for x in ss)
            if sh > 0:
                exit_price = sum(x["shares"] * x["price"] for x in ss) / sh
                exit_day = int(str(ss[-1]["at"])[:10].replace("-", ""))
        if exit_day is None:
            exit_day = latest
            exit_price = bars.get(latest, (None, None))[0]
        if bd not in cal_idx:
            continue
        end_i = cal_idx.get(exit_day, len(cal) - 1)
        win = cal[cal_idx[bd]:end_i + 1]
        closes = [bars[d][0] for d in win if d in bars]
        highs = [bars[d][1] for d in win if d in bars]
        if exit_price is None:
            exit_price = closes[-1] if closes else b["price"]
        hold = cal_idx[exit_day] - cal_idx[bd] if exit_day in cal_idx else len(win) - 1
        actual_rows.append({
            "at": b["at"], "code": b["code"], "name": b["name"], "n": b["n"],
            "buy_price": b["price"], "shares": b["shares"],
            "buy_day": bd, "exit_day": exit_day, "exit_price": round(exit_price, 2),
            "hold_days": hold, "final_ret": round((exit_price / b["price"] - 1) * 100, 2),
            "max_close_ret": round(max((c / b["price"] - 1) * 100 for c in closes), 2) if closes else None,
            "max_high_ret": round(max((h / b["price"] - 1) * 100 for h in highs), 2) if highs else None,
        })

    rows = []
    for p in picks:
        bars = bars_for(p["code"])
        pick_day = int(p["date"])
        # 匹配实际买入：同代码，推荐日附近（前 1 到后 3 个交易日）
        cands = []
        for b in buys:
            if b["code"] != p["code"]:
                continue
            bd = int(str(b["at"])[:10].replace("-", ""))
            if bd not in cal_idx or pick_day not in cal_idx:
                continue
            gap = cal_idx[bd] - cal_idx[pick_day]
            if -1 <= gap <= 3:
                cands.append((abs(gap), b))
        cands.sort(key=lambda x: x[0])
        actual = cands[0][1] if cands else None

        if actual:
            entry_day = int(str(actual["at"])[:10].replace("-", ""))
            entry = actual["price"]
            # 匹配卖出（买入之后、同代码）
            ss = [s for s in sells if s["code"] == p["code"] and s["at"] > actual["at"]]
            exit_day, exit_price = None, None
            if ss:
                shares = sum(x["shares"] for x in ss)
                if shares > 0:
                    exit_price = sum(x["shares"] * x["price"] for x in ss) / shares
                    exit_day = int(str(ss[-1]["at"])[:10].replace("-", ""))
            if exit_day is None:
                exit_day = latest
                exit_price = bars.get(latest, (None, None))[0]
        else:
            entry_day = pick_day
            entry = p["price"]
            exit_day = None
            exit_price = None

        # 观察窗口：成交→退出/至今；未成交→推荐后 5 个交易日
        if actual:
            start_i = cal_idx.get(entry_day, 0)
            end_i = cal_idx.get(exit_day, start_i) if exit_day else len(cal) - 1
        else:
            start_i = cal_idx.get(pick_day, 0)
            end_i = min(start_i + LOOKAHEAD, len(cal) - 1)
        window_days = [d for d in cal[start_i:end_i + 1]]

        closes = [(d, bars[d][0]) for d in window_days if d in bars]
        highs = [(d, bars[d][1]) for d in window_days if d in bars]
        max_close_ret = max(((c / entry - 1) * 100 for _, c in closes), default=None)
        max_high_ret = max(((h / entry - 1) * 100 for _, h in highs), default=None)
        # 数据有效性：无当日K线 / 窗口内出现 >25% 的除权式跳空 → 标记异常，不计入统计
        valid = True
        why = ""
        if entry_day not in bars:
            valid, why = False, "推荐日无K线"
        elif abs(bars[entry_day][0] / entry - 1) > 0.20:
            valid, why = False, "推荐价/日线不一致(疑除权)"
        else:
            seq = [bars[d][0] for d in window_days if d in bars]
            for a, b in zip(seq, seq[1:]):
                if a > 0 and (b / a - 1) < -0.25:
                    valid, why = False, "疑除权/异常跳空"
                    break
        if exit_price is None:
            exit_price = closes[-1][1] if closes else entry
        final_ret = (exit_price / entry - 1) * 100 if entry else None
        hold_days = (cal_idx[exit_day] - cal_idx[entry_day]) if (exit_day in cal_idx and entry_day in cal_idx) else (
            len(window_days) - 1 if window_days else None)

        rows.append({
            "date": p["date"], "code": p["code"], "name": p["name"], "pick_price": p["price"],
            "actual": actual is not None,
            "buy_day": entry_day, "buy_price": entry,
            "exit_day": exit_day, "exit_price": exit_price,
            "hold_days": hold_days,
            "final_ret": round(final_ret, 2) if final_ret is not None else None,
            "max_close_ret": round(max_close_ret, 2) if max_close_ret is not None else None,
            "max_high_ret": round(max_high_ret, 2) if max_high_ret is not None else None,
            "valid": valid, "why": why,
        })

    # 汇总
    valid_rows = [r for r in rows if r.get("valid")]
    def cnt(rs, key, th):
        return sum(1 for r in rs if (r.get(key) or -999) >= th)

    exec_n = sum(1 for r in rows if r["actual"])
    ai = [r for r in actual_rows if r["buy_day"] in cal_idx]
    exec_valid = sum(1 for r in valid_rows if r["actual"])
    hold_vals = [r["hold_days"] for r in ai if r["hold_days"] is not None]
    lines = ["# 股鉴推荐复盘（至 2026-11-23）", "",
             "> 数据：`data/picks_daily.json`（结构化推荐 33 条）+ 历史账本（实际成交 14 笔）+ 通达信日线（已到 2026-11-23）。",
             "- 关键事实：v5 随机池上线后，**每日推荐（选股器）与实际买入不是同一套**；33 条推荐里只有 3 条被实际买入。",
             ""]
    lines.append(f"- 结构化推荐：**{len(rows)} 条**（2026-10-21 ~ 2026-11-23）；有效 {len(valid_rows)} 条，异常 {len(rows)-len(valid_rows)} 条")
    lines.append(f"- 其中实际成交：**{exec_n} 条**；未成交 {len(rows) - exec_n} 条")
    if hold_vals:
        lines.append(f"- 实际成交持有天数（交易日）：平均 {sum(hold_vals)/len(hold_vals):.1f} 天，最短 {min(hold_vals)}，最长 {max(hold_vals)}")
    lines += ["", "## 一、实际模拟盘成交（14 笔，A/B 账本）", "",
              "| 买入时间 | 代码 | 名称 | 实例数 | 买入价 | 持有天数 | 卖出/现价 | 最终收益 | 期间最高(收盘) | 期间最高(最高价) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    def f(v):
        return "—" if v is None else f"{v:+.2f}%"
    for r in ai:
        lines.append(f"| {r['at'][:16]} | {r['code']} | {r['name']} | {r['n']} | {r['buy_price']:.2f} | {r['hold_days']} | "
                     f"{r['exit_price']:.2f} | {f(r['final_ret'])} | {f(r['max_close_ret'])} | {f(r['max_high_ret'])} |")
    dist = {}
    for r in ai:
        dist[r["hold_days"]] = dist.get(r["hold_days"], 0) + 1
    lines += ["", "**持有天数分布**：" + "、".join(f"{k}天×{v}" for k, v in sorted(dist.items())),
              "", "**实际成交汇总**", "",
              "| 口径 | ≥1% | ≥3% | ≥5% |",
              "|---|---|---|---|",
              f"| 期间最高（收盘） | {cnt(ai,'max_close_ret',1)}/{len(ai)} | {cnt(ai,'max_close_ret',3)}/{len(ai)} | {cnt(ai,'max_close_ret',5)}/{len(ai)} |",
              f"| 期间最高（最高价） | {cnt(ai,'max_high_ret',1)}/{len(ai)} | {cnt(ai,'max_high_ret',3)}/{len(ai)} | {cnt(ai,'max_high_ret',5)}/{len(ai)} |",
              f"| 最终实现/浮动 | {cnt(ai,'final_ret',1)}/{len(ai)} | {cnt(ai,'final_ret',3)}/{len(ai)} | {cnt(ai,'final_ret',5)}/{len(ai)} |",
              "", "## 关键发现", "",
              f"- 实际成交 {len(ai)} 个事件（A/B 镜像已合并），持有天数平均 {sum(hold_vals)/len(hold_vals):.1f} 天（0–{max(hold_vals)} 天）；"
              f"最终 ≥1% 仅 {cnt(ai,'final_ret',1)}/{len(ai)}，≥3% {cnt(ai,'final_ret',3)}/{len(ai)}，≥5% {cnt(ai,'final_ret',5)}/{len(ai)}。",
              f"- 但盘中最高价 ≥1% 有 {cnt(ai,'max_high_ret',1)}/{len(ai)}，≥3% {cnt(ai,'max_high_ret',3)}/{len(ai)}，≥5% {cnt(ai,'max_high_ret',5)}/{len(ai)}：波动有，落袋少。",
              f"- 推荐影子跟踪（有效 {len(valid_rows)} 条）：最高价 ≥1% {cnt(valid_rows,'max_high_ret',1)}/{len(valid_rows)}、≥3% {cnt(valid_rows,'max_high_ret',3)}/{len(valid_rows)}、≥5% {cnt(valid_rows,'max_high_ret',5)}/{len(valid_rows)}；"
              f"但推荐后第 5 日收益 ≥1% 只有 {cnt(valid_rows,'final_ret',1)}/{len(valid_rows)}。",
              "- 结构性问题：v5 随机池上线后，**每日推荐（选股器）与实际买入脱节**——33 条推荐只有 3 条被实际买入；"
              "推荐表现不代表实盘表现，需要决策：恢复按推荐执行，或停止旧选股器推荐记录。",
              "- 数据覆盖：结构化推荐从 2026-10-21 起（更早只有报告）；历史账本来自备份合并，个别早期交易可能缺失；日线不复权，异常行已标注。",
              "", "## 二、每日推荐的市场表现（33 条，未成交按推荐后 5 个交易日跟踪）", "",
              "> 未成交推荐只做“影子跟踪”，不代表实际收益；异常行（除权/无K线）已标记并从汇总剔除。",
              "",
              "| 口径 | ≥1% | ≥3% | ≥5% |",
              "|---|---|---|---|",
              f"| 期间最高（收盘） | {cnt(valid_rows,'max_close_ret',1)}/{len(valid_rows)} | {cnt(valid_rows,'max_close_ret',3)}/{len(valid_rows)} | {cnt(valid_rows,'max_close_ret',5)}/{len(valid_rows)} |",
              f"| 期间最高（最高价） | {cnt(valid_rows,'max_high_ret',1)}/{len(valid_rows)} | {cnt(valid_rows,'max_high_ret',3)}/{len(valid_rows)} | {cnt(valid_rows,'max_high_ret',5)}/{len(valid_rows)} |",
              f"| 推荐后第5日收益 | {cnt(valid_rows,'final_ret',1)}/{len(valid_rows)} | {cnt(valid_rows,'final_ret',3)}/{len(valid_rows)} | {cnt(valid_rows,'final_ret',5)}/{len(valid_rows)} |",
              "", "## 逐票明细", "",
              "| 日期 | 代码 | 名称 | 推荐价 | 实际成交 | 持有天数 | 卖出/现价 | 最终收益 | 期间最高(收盘) | 期间最高(最高价) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        def f(v):
            return "—" if v is None else f"{v:+.2f}%"
        notes = "" if r.get("valid") else ("｜" + r.get("why", "异常"))
        lines.append(f"| {r['date']} | {r['code']} | {r['name']} | {r['pick_price']:.2f} | "
                     f"{'✅' if r['actual'] else '—'} | {r['hold_days'] if r['hold_days'] is not None else '—'} | "
                     f"{r['exit_price']:.2f} | {f(r['final_ret'])} | {f(r['max_close_ret'])} | {f(r['max_high_ret'])}{notes} |")
    lines += ["", "## 说明", "",
              "- 结构化推荐从 2026-10-21 起；10-17~10-20 早期只有报告、无结构化推荐记录。",
              "- 未成交的推荐按推荐价起、跟踪后 5 个交易日；实际成交按真实买卖价/交易日。",
              "- 最终收益=卖出价（或最新收盘）相对买入价；期间最高=持有窗口内收盘/最高价相对买入价。",
              "- 推荐与实盘执行分离（v5 随机池）：推荐表现只能说明选股器，最终收益看实际成交。",
              "- 数据为不复权日线；除权/跳空异常行已标记剔除，避免把 10 转 10 当成 -50%。",
              "- 本复盘只统计模拟盘，不构成投资建议。"]
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    acsv = OUT_CSV.with_name(OUT_CSV.stem + '_actual.csv')
    with acsv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ai[0].keys()))
        w.writeheader()
        w.writerows(ai)
    print(f"  ✅ 报告: {OUT_MD} ｜ CSV: {OUT_CSV}")
    print(f"  记录 {len(rows)} 条，成交 {exec_n} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
