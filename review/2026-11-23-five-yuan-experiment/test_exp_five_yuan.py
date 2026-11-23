# -*- coding: utf-8 -*-
"""5元股 +5% 链式实验单元测试（2026-11-23，合成数据，不读 TDX）。"""
from __future__ import annotations

import unittest

from stocklens import exp_five_yuan as fx


def _bars():
    # date: (close, high)
    return {
        "600000": {20260101: (4.0, 4.1), 20260102: (4.0, 4.3), 20260103: (4.2, 4.3)},
        "600001": {20260101: (3.0, 3.1), 20260102: (3.0, 3.2), 20260103: (3.2, 3.3)},
        "600002": {20260101: (3.0, 3.1), 20260102: (3.0, 3.2), 20260103: (3.2, 3.3)},
        "600003": {20260101: (5.5, 5.6), 20260102: (5.6, 5.7), 20260103: (5.7, 5.8)},
    }


CAL = [20260101, 20260102, 20260103]


class TExpFiveYuan(unittest.TestCase):
    def test_t_plus_1_and_high_mode(self):
        r = fx.run(_bars(), CAL, sell_mode="high", seed=1)
        # 600000 在 day2 最高 4.3 ≥ 4.2，day1 的 4.1 不够；day2 卖出，持有 1 天
        first = r["trades"][0]
        self.assertEqual(first["buy_day"], 20260101)
        self.assertEqual(first["sell_day"], 20260102)
        self.assertEqual(first["hold_days"], 1)
        self.assertAlmostEqual(first["sell_price"], 4.2, places=3)

    def test_close_mode_fills_at_close(self):
        r = fx.run(_bars(), CAL, sell_mode="close", seed=1)
        # day2 收盘 4.0 未到 4.2；day3 收盘 4.2 才到 → 持有 2 天，按收盘价成交
        first = r["trades"][0]
        self.assertEqual(first["hold_days"], 2)
        self.assertAlmostEqual(first["sell_price"], 4.2, places=3)

    def test_price_filter_5(self):
        r = fx.run(_bars(), CAL, sell_mode="high", seed=1)
        for t in r["trades"]:
            self.assertIn(t["code"], ("600000", "600001", "600002"))
        self.assertNotIn("600003", [t["code"] for t in r["trades"]])

    def test_rebuy_same_day(self):
        # 用只有 2 天的数据：day2 卖出后当天应能再买一只
        bars = {
            "600000": {20260101: (4.0, 4.1), 20260102: (4.0, 4.3)},
            "600001": {20260101: (3.0, 3.1), 20260102: (3.0, 3.0)},
        }
        r = fx.run(bars, [20260101, 20260102], sell_mode="high", seed=1)
        self.assertGreaterEqual(len(r["trades"]), 1)
        self.assertIsNotNone(r["open"])  # 卖出后当天买入，期末仍持仓

    def test_open_stats(self):
        # 全部不涨：只有一笔未平仓
        bars = {"600000": {20260101: (4.0, 4.0), 20260102: (4.0, 4.0), 20260103: (4.0, 4.0)}}
        r = fx.run(bars, CAL, sell_mode="high", seed=1)
        self.assertEqual(r["trades"], [])
        st = fx.stats([], [r["open"]])
        self.assertEqual(st["total_positions"], 1)
        self.assertEqual(st["hit_rate_pct"], 0.0)
        self.assertEqual(st["avg_open_hold"], 2)

    def test_stats_distribution(self):
        trades = [{"hold_days": h} for h in (1, 2, 3, 4, 5, 6, 11)]
        st = fx.stats(trades, [])
        self.assertEqual(st["trades"], 7)
        self.assertEqual(st["within_1d_pct"], 14.3)
        self.assertEqual(st["within_3d_pct"], 42.9)
        self.assertEqual(st["within_5d_pct"], 71.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
