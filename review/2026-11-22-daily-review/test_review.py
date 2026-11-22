# -*- coding: utf-8 -*-
"""复盘统计 v2 测试（2026-11-22，含审阅回归 R6/R7/R9/R10）。"""
from __future__ import annotations

import unittest
from unittest import mock

from stocklens import review


def _ledgers() -> dict:
    return {
        "A": {
            "start_capital": 10000,
            "cash": 8000,
            "equity": [
                {"date": "20261117", "equity": 10000},
                {"date": "20261118", "equity": 11000},
                {"date": "20261119", "equity": 10500},
                {"date": "20261120", "equity": 10900},
            ],
            "slots": [
                {"name": "测试股", "code": "600000", "shares": 100, "cost": 5.0,
                 "sell_due": "20260101"},
            ],
            "trades": [
                {"at": "2026-11-19 14:50:00", "action": "卖出", "code": "600001",
                 "name": "甲", "shares": 100, "price": 10.0, "pnl": 200.0, "fee": 5.0},
                {"at": "2026-11-19 14:51:00", "action": "卖出", "code": "600002",
                 "name": "乙", "shares": 100, "price": 10.0, "pnl": -100.0, "fee": 5.0},
                {"at": "2026-11-20 14:52:00", "action": "卖出", "code": "600003",
                 "name": "丙", "shares": 100, "price": 10.0, "pnl": 300.0, "fee": 5.0},
                {"at": "2026-11-20 14:53:00", "action": "买入", "code": "600009",
                 "name": "新买", "shares": 100, "price": 10.0},
            ],
        },
        "B": {"start_capital": 5000, "cash": 5000, "slots": [], "trades": []},
        "C": {},
    }


class TReview(unittest.TestCase):
    def test_metrics(self):
        res = review.compute(day="20261120", ledgers=_ledgers(), save=False)
        a = res["instances"]["A"]
        self.assertEqual(a["closed_trades"], 3)
        self.assertEqual(a["win_rate_pct"], 66.7)
        self.assertAlmostEqual(a["expectancy"], round((200 - 100 + 300) / 3, 2))
        self.assertAlmostEqual(a["profit_factor"], 5.0)
        self.assertEqual(a["pnl_total"], 400.0)
        self.assertEqual(a["today"]["sells"], 1)
        self.assertEqual(len(a["overdue"]), 1)
        self.assertAlmostEqual(a["max_drawdown_pct"], -4.55, places=1)

    def test_combined_fields_present(self):
        res = review.compute(day="20261120", ledgers=_ledgers(), save=False)
        c = res["combined"]
        for key in ("avg_win", "avg_loss", "profit_factor", "expectancy",
                    "fees_total", "max_drawdown_pct", "overdue"):
            self.assertIn(key, c, key)
        self.assertEqual(c["closed_trades"], 3)
        self.assertEqual(c["pnl_total"], 400.0)

    def test_max_drawdown_counts_start_and_zero(self):
        """R7/R8：本金也当峰值；跌到 0 = -100%。"""
        led = {"start_capital": 10000, "equity": [{"date": "20261117", "equity": 9500}], "trades": []}
        res = review.compute(day="20261117", ledgers={"A": led, "B": {}, "C": {}}, save=False)
        self.assertAlmostEqual(res["instances"]["A"]["max_drawdown_pct"], -5.0, places=1)

        led0 = {"start_capital": 10000, "equity": [
            {"date": "20261117", "equity": 10000},
            {"date": "20261118", "equity": 0}], "trades": []}
        res0 = review.compute(day="20261118", ledgers={"A": led0, "B": {}, "C": {}}, save=False)
        self.assertAlmostEqual(res0["instances"]["A"]["max_drawdown_pct"], -100.0, places=1)

    def test_benchmark_short_series_none(self):
        with mock.patch("stocklens.tdx.index_series", return_value={"date": [20261117], "close": [3500.0]}):
            self.assertIsNone(review._benchmark("20261117"))

    def test_benchmark_as_of_day(self):
        dates = [20261110, 20261111, 20261112, 20261113, 20261114,
                 20261117, 20261118, 20261119, 20261120, 20261123, 20261124]
        closes = [3400 + i * 10 for i in range(len(dates))]
        with mock.patch("stocklens.tdx.index_series", return_value={"date": dates, "close": closes}):
            bm = review._benchmark("20261120")
        self.assertIsNotNone(bm)
        self.assertEqual(bm["last_date"], "20261120")

    def test_summary_no_none(self):
        text = review.summary_text(review.compute(day="20261120",
                                                 ledgers={"A": {}, "B": {}, "C": {}}, save=False))
        self.assertNotIn("None", text)

    def test_save_false_does_not_write(self):
        import pathlib
        out = pathlib.Path(review.OUT)
        before = out.read_text(encoding="utf-8") if out.exists() else None
        review.compute(day="20261120", ledgers=_ledgers(), save=False)
        after = out.read_text(encoding="utf-8") if out.exists() else None
        self.assertEqual(before, after)

    def test_suspicious_early_equity_excluded(self):
        """早期旧口径低点不应制造 -80% 回撤。"""
        led = {"start_capital": 10000, "equity": [
            {"date": "20261118", "equity": 1000},
            {"date": "20261119", "equity": 9800},
            {"date": "20261120", "equity": 9700}], "trades": []}
        res = review.compute(day="20261120", ledgers={"A": led, "B": {}, "C": {}}, save=False)
        a = res["instances"]["A"]
        self.assertGreater(a["max_drawdown_pct"], -10.0)
        self.assertEqual(a["equity_points_excluded"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
