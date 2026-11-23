# -*- coding: utf-8 -*-
"""股鉴推荐复盘 v2（2026-11-23，经 WorkBuddy 交叉验证后修正）。

相对 v1 的修正：
  1. 推荐影子「期间最高」不再包含推荐日当天（推荐都在收盘后生成，含当日=前视）；
  2. 实际成交「期间最高」只算买入日之后（买入在 14:2x/14:5x，当日最高可能发生在买入前）；
  3. 阈值判定用未舍入值（修复 3% 边界：华银电力 2.995% 不再算 ≥3%）；
  4. 第 5 日收益拆两栏：满 5 交易日（22 条）与不足 5 日（按最新收盘填充，9 条）；
  5. 除权/极端判定：无K线剔除；连续跌停/极端行情保留并标注（不再声称“异常已剔除”）；
  6. 「推荐→执行」改成“同日重合/先买后推荐”口径，不把随机池买入当成推荐被执行。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\自动炒股")
PKG = ROOT / "opensource" / "gujian-data" / "review" / "2026-11-23-recommendation-review"
OUT_MD = ROOT / "docs" / "推荐复盘-20261123.md"
OUT_CSV = ROOT / "_data" / "recommendation_review_20261123.csv"


def read_csv(name: str) -> list[dict]:
    with (PKG / name).open("r", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load() -> tuple[list[dict], list[dict], dict[str, dict[int, tuple[float, float]]], list[int]]:
    picks = read_csv("picks_daily.csv")
    trades = read_csv("trades_merged.csv")
    bars: dict[str, dict[int, tuple[float, float]]] = {}
    for r in read_csv("bars_windows.csv"):
        bars.setdefault(r["code"], {})[int(r["date"])] = (float(r["close"]), float(r["high"]))
    cal = [int(r["date"]) for r in read_csv("calendar.csv")]
    return picks, trades, bars, cal


def pct(a: float, b: float) -> float:
    return (a / b - 1) * 100 if b else 0.0


def main() -> int:
    picks, trades, bars, cal = load()
    idx = {d: i for i, d in enumerate(cal)}
    last = cal[-1]

    # ---------- 推荐影子 ----------
    shadow = []
    for p in picks:
        code, day, price = p["code"], int(p["date"]), float(p["price"])
        b = bars.get(code, {})
        di = idx.get(day)
        if day not in b or di is None:
            shadow.append({"date": p["date"], "code": code, "name": p["name"], "price": price,
                           "valid": False, "why": "推荐日无K线"})
            continue
        post = cal[di + 1:di + 6]  # 推荐日之后最多 5 个交易日
        if not post:
            shadow.append({"date": p["date"], "code": code, "name": p["name"], "price": price,
                           "valid": False, "why": "推荐日为最新交易日，无后续数据"})
            continue
        closes = [(d, b[d][0]) for d in post if d in b]
        highs = [(d, b[d][1]) for d in post if d in b]
        if not closes:
            shadow.append({"date": p["date"], "code": code, "name": p["name"], "price": price,
                           "valid": False, "why": "后续无K线"})
            continue
        mc = max(pct(c, price) for _, c in closes)
        mh = max(pct(h, price) for _, h in highs) if highs else None
        # 第5日：满 5 日给真值；不足 5 日给“最新收盘填充值”，单列
        full5 = len(post) >= 5
        final = pct(closes[-1][1], price)
        extreme = False
        cs = [c for _, c in closes]
        if cs and (pct(cs[-1], price) < -40):
            extreme = True
        for a, bb in zip(cs, cs[1:]):
            if pct(bb, a) <= -15:
                extreme = True  # 连续跌停/极端波动（保留并标注，不当除权剔除）
        shadow.append({"date": p["date"], "code": code, "name": p["name"], "price": price,
                       "valid": True, "full5": full5, "hold_days": len(post),
                       "max_close": mc, "max_high": mh, "final": final,
                       "extreme": extreme, "why": ""})

    # ---------- 实际成交 ----------
    buys = [t for t in trades if t["action"] == "买入"]
    sells = [t for t in trades if t["action"] == "卖出"]
    groups: dict[tuple, list] = {}
    for b in buys:
        groups.setdefault((b["code"], b["at"][:10], b["price"]), []).append(b)
    actual = []
    for (_code, _bd, _price), bs in groups.items():
        code = bs[0]["code"]
        bd = int(bs[0]["at"][:10].replace("-", ""))
        price = float(bs[0]["price"])
        di = idx.get(bd)
        if di is None:
            continue
        ss = [s for s in sells if s["code"] == code and s["at"] > bs[0]["at"]]
        if ss:
            sh = sum(float(s["shares"]) for s in ss)
            exitp = sum(float(s["shares"]) * float(s["price"]) for s in ss) / sh
            exd = int(ss[-1]["at"][:10].replace("-", ""))
        else:
            exd, exitp = last, bars.get(code, {}).get(last, (price, price))[0]
        ei = idx.get(exd, len(cal) - 1)
        post = cal[di + 1:ei + 1]  # 买入日之后
        closes = [(d, bars[code][d][0]) for d in post if d in bars.get(code, {})]
        highs = [(d, bars[code][d][1]) for d in post if d in bars.get(code, {})]
        actual.append({
            "at": bs[0]["at"], "code": code, "name": bs[0]["name"], "n": len(bs),
            "buy_price": price, "hold": ei - di, "exit_price": round(exitp, 2),
            "final": pct(exitp, price),
            "max_close": max((pct(c, price) for _, c in closes), default=None),
            "max_high": max((pct(h, price) for _, h in highs), default=None),
            "post_bars": len(post),
        })

    # ---------- 推荐 vs 执行（同代码、同日 / 先买后推荐） ----------
    same_day, earlier = [], []
    for b in buys:
        bd = int(b["at"][:10].replace("-", ""))
        for p in picks:
            if p["code"] != b["code"]:
                continue
            pd = int(p["date"])
            if pd == bd:
                same_day.append((b, p))
            elif bd < pd:
                earlier.append((b, p))
    conflict = sorted({(b["code"], b["name"], b["at"][:10], p["date"]) for b, p in earlier})

    # ---------- 汇总 ----------
    vals = [s for s in shadow if s["valid"]]
    full5 = [s for s in vals if s.get("full5")]
    def cnt(rows, key, th):
        return sum(1 for r in rows if r.get(key) is not None and r[key] >= th)

    ameas = [a for a in actual if a["max_high"] is not None]
    hold_dist: dict[int, int] = {}
    for a in actual:
        hold_dist[a["hold"]] = hold_dist.get(a["hold"], 0) + 1

    lines = [
        "# 股鉴推荐复盘（至 2026-11-23，v2 交叉验证修正版）",
        "",
        "> 数据：`data/picks_daily.json`（推荐 33 条）+ 历史账本（14 笔买入→13 事件）+ 通达信不复权日线（到 2026-11-23）。",
        "> v2 修正：推荐影子不含推荐日、实际成交不含买入日、阈值用未舍入值、第5日拆满/不满两种口径、极端行情保留并标注。",
        "",
        f"- 推荐 33 条（2026-10-21 ~ 11-23），有效 {len(vals)} 条：2 条推荐日无 K 线，1 条为最新交易日无后续。",
        f"- 实际买入 14 笔 → A/B 镜像合并 **13 个事件**；其中 4 个当日卖出，9 个有“买入日之后”的盘中数据。",
        f"- 推荐与执行：**同代码同日重合 3 条；先买后推荐 1 条**（v5 随机池与推荐无执行关系）。",
        "",
        "## 一、实际成交（13 事件）",
        "",
        "持有交易日分布：" + "、".join(f"{k}天×{v}" for k, v in sorted(hold_dist.items())),
        "",
        "| 买入时间 | 代码 | 名称 | 实例数 | 买入价 | 持有 | 卖出/现价 | 最终收益 | 买入后最高(收盘) | 买入后最高(最高价) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    def f(v):
        return "—" if v is None else f"{v:+.2f}%"
    for a in sorted(actual, key=lambda x: x["at"]):
        lines.append(f"| {a['at'][:16]} | {a['code']} | {a['name']} | {a['n']} | {a['buy_price']:.2f} | "
                     f"{a['hold']} | {a['exit_price']:.2f} | {f(a['final'])} | {f(a['max_close'])} | {f(a['max_high'])} |")
    lines += [
        "",
        f"**汇总（最终收益）**：≥1% {cnt(actual,'final',1)}/13 ｜ ≥3% {cnt(actual,'final',3)}/13 ｜ ≥5% {cnt(actual,'final',5)}/13",
        f"**汇总（买入日之后盘中最高，n={len(ameas)}）**：≥1% {cnt(ameas,'max_high',1)}/{len(ameas)} ｜ "
        f"≥3% {cnt(ameas,'max_high',3)}/{len(ameas)} ｜ ≥5% {cnt(ameas,'max_high',5)}/{len(ameas)}",
        "> 注：4 个当日卖出的样本无“买入后”盘中数据，未计入；含买入日当天的最低价口径为 9/6/5（有前视风险，不作为主口径）。",
        "",
        "## 二、每日推荐的市场表现（影子跟踪）",
        "",
        f"**口径**：推荐价买入，跟踪推荐日之后最多 5 个交易日；不含推荐日当天（推荐均在 15:00 后生成）。有效 {len(vals)} 条。",
        "",
        "| 口径 | ≥1% | ≥3% | ≥5% |",
        "|---|---|---|---|",
        f"| 期间最高（收盘价，n={len(vals)}） | {cnt(vals,'max_close',1)}/{len(vals)} | {cnt(vals,'max_close',3)}/{len(vals)} | {cnt(vals,'max_close',5)}/{len(vals)} |",
        f"| 期间最高（盘中最高价，n={len(vals)}） | {cnt(vals,'max_high',1)}/{len(vals)} | {cnt(vals,'max_high',3)}/{len(vals)} | {cnt(vals,'max_high',5)}/{len(vals)} |",
        f"| 第5日收益（满5日，n={len(full5)}） | {cnt(full5,'final',1)}/{len(full5)} | {cnt(full5,'final',3)}/{len(full5)} | {cnt(full5,'final',5)}/{len(full5)} |",
        f"| 第5日收益（不足5日按最新收盘，单列） | {cnt([s for s in vals if not s.get('full5')],'final',1)}/{len([s for s in vals if not s.get('full5')])} | "
        f"{cnt([s for s in vals if not s.get('full5')],'final',3)}/{len([s for s in vals if not s.get('full5')])} | "
        f"{cnt([s for s in vals if not s.get('full5')],'final',5)}/{len([s for s in vals if not s.get('full5')])} |",
        "",
        "## 三、逐票明细（推荐影子）",
        "",
        "| 日期 | 代码 | 名称 | 推荐价 | 跟踪天数 | 第5日/最新收益 | 期间最高(收盘) | 期间最高(最高价) | 备注 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in shadow:
        if not s["valid"]:
            lines.append(f"| {s['date']} | {s['code']} | {s['name']} | {s['price']:.2f} | — | — | — | — | {s['why']} |")
            continue
        note = []
        if not s.get("full5"):
            note.append("跟踪不足5日")
        if s.get("extreme"):
            note.append("极端行情（连续跌停）")
        lines.append(f"| {s['date']} | {s['code']} | {s['name']} | {s['price']:.2f} | {s['hold_days']} | "
                     f"{f(s['final'])} | {f(s['max_close'])} | {f(s['max_high'])} | {'；'.join(note)} |")
    lines += [
        "",
        "## 四、结论（修正后）",
        "",
        "1. **实际成交**：13 个事件里，最终 ≥1% 只有 2 个（15%），≥3% 2 个，≥5% 0 个；"
        "买入日之后盘中最高 ≥1% 的 5/9、≥3% 3/9、≥5% 2/9——冲高比例仍高，但兑现比例低。",
        "2. **推荐影子**：推荐后盘中最高 ≥1% 18/30、≥3% 12/30、≥5% 9/30；"
        "但第 5 个交易日仍 ≥1% 的严格样本只有 3/22（14%）。结论方向成立，但强度比 v1 弱。",
        "3. **“不会守”要分开看**：实际持有平均 1.2 天（0–3 天），很大程度是 T+2/T+3 到期机械卖出；"
        "不能直接归因于“选股后自然衰减”或“拿不住”。",
        "4. **推荐与执行脱节**：同代码同日重合仅 3 条，另有 1 条先买后推荐（600103 青山纸业 11-17 买、11-19 推荐）；"
        "v5 随机池买入与每日推荐没有因果关系，推荐表现不能当实盘表现。",
        "5. **样本/数据限制**：影子 31 条、实际 13 事件、窗口 34 天、单一市场环境；日线不复权；"
        "688613 奥精医疗为连续跌停（非除权），保留为极端样本；结论只作初步观察。",
        "",
        "## 五、交叉验证修正记录（2026-11-23）",
        "",
        "- WorkBuddy 复核发现：3% 计数受 round 影响（17→16）、第 5 日混入不足 5 日样本、"
        "“期间最高”含买入日/推荐日存在前视、推荐匹配规则未披露——**均已采纳并修正**。",
        "- WorkBuddy 认为 688613 是“除权漏判”：经复核，其为科创板连续 3 日跌停（约 -20%/日），"
        "**不是除权**；已改为“极端行情”标注保留，不再声称“异常已剔除”。",
        "- 修正后方向性结论不变：冲高比例高、兑现比例低；推荐与 v5 执行无因果关系。",
        "",
        "> 本复盘只统计模拟盘，不构成投资建议。",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    # CSV 输出（影子 + 实际）
    fields = ["date", "code", "name", "price", "valid", "why", "full5", "hold_days",
              "max_close", "max_high", "final", "extreme"]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for s in shadow:
            w.writerow(s)
    af = OUT_CSV.with_name(OUT_CSV.stem + "_actual.csv")
    with af.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(actual[0].keys()))
        w.writeheader()
        w.writerows(actual)
    print(f"  ✅ {OUT_MD}")
    uniq_same = {(b["code"], b["at"][:10]) for b, _p in same_day}
    print(f"  影子 {len(vals)} 条（满5日 {len(full5)}）｜实际 {len(actual)} 事件｜同日重合 {len(uniq_same)}｜先买后推荐 {len(conflict)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
