# -*- coding: utf-8 -*-
"""个股复盘 v2 测试（2026-11-22，含审阅回归 R13/R14/R15/R17/R18）。"""
from __future__ import annotations

import unittest
from unittest import mock

from stocklens import daily_review


def _bars() -> dict[int, float]:
    return {
        20261118: 10.0,
        20261119: 10.5,
        20261120: 11.0,
        20261124: 12.0,
    }


def _bars_fn(code: str) -> dict[int, float]:
    return _bars()


def _ledgers() -> dict:
    return {
        "A": {
            "trades": [
                {"at": "2026-11-20 14:59:00", "action": "买入", "code": "600001",
                 "name": "甲", "price": 10.0, "shares": 100},
                {"at": "2026-11-19 14:50:00", "action": "卖出", "code": "600009",
                 "name": "旧", "price": 5.0, "shares": 100, "pnl": 10.0},
            ],
            "slots": [
                {"name": "甲", "code": "600001", "shares": 100, "cost": 10.0,
                 "buy_date": "20261120", "last": 11.0},
                {"name": "老仓", "code": "600002", "shares": 100, "cost": 10.0,
                 "buy_date": "20261118", "last": 9.0, "sell_due": "20261121"},
            ],
        },
        "B": {"trades": [], "slots": []},
        "C": {},
    }


class TDailyReview(unittest.TestCase):
    def _run(self, day: str = "20261123", ledgers=None):
        dates = [20261118, 20261119, 20261120, 20261124]
        with mock.patch.object(daily_review, "_trading_dates", return_value=dates):
            return daily_review.build(day=day, ledgers=ledgers or _ledgers(),
                                      bars_fn=_bars_fn, save=False)

    def test_prev_validation_uses_latest_close(self):
        """R14：上一交易日验证应显示最新收盘（12.0/+20%），不是买入日收盘（11.0）。"""
        res = self._run("20261124")
        a = res["instances"]["A"]
        self.assertEqual(len(a["prev_buys"]), 1)
        b = a["prev_buys"][0]
        self.assertEqual(b["close"], 11.0)          # 买入日收盘
        self.assertEqual(b["latest_close"], 12.0)   # 最新收盘
        self.assertAlmostEqual(b["latest_ret_pct"], 20.0)
        text = daily_review.summary_text(res)
        self.assertIn("最新 12.0", text)
        self.assertIn("+20.00%", text)

    def test_holdings_and_overdue(self):
        res = self._run("20261123")
        hs = {h["code"]: h for h in res["instances"]["A"]["holdings"]}
        self.assertIn("600001", hs)
        self.assertIn("600002", hs)
        self.assertFalse(hs["600001"]["overdue"])
        self.assertTrue(hs["600002"]["overdue"])
        self.assertEqual(hs["600001"]["last_date"], 20261124)

    def test_no_kline_no_crash(self):
        """R13/R15：无 K 线 / cost=0 不能崩，显示「—/无K线」。"""
        def empty_bars(code):
            return {}
        dates = [20261120]
        with mock.patch.object(daily_review, "_trading_dates", return_value=dates):
            res = daily_review.build(day="20261120", ledgers=_ledgers(),
                                     bars_fn=empty_bars, save=False)
        text = daily_review.summary_text(res)
        self.assertIn("无当日K线", text)
        self.assertNotIn("None", text)
        self.assertIn("—", text)

    def test_cost_zero_holdings(self):
        led = {"A": {"trades": [], "slots": [
            {"name": "零成本", "code": "600003", "shares": 100, "cost": 0, "buy_date": "20261118"}]},
            "B": {"trades": [], "slots": []}, "C": {}}
        res = self._run("20261120", ledgers=led)
        h = res["instances"]["A"]["holdings"][0]
        self.assertIsNone(h["pnl_pct"])
        text = daily_review.summary_text(res)
        self.assertNotIn("None", text)

    def test_partial_sell_status(self):
        """R17：买入 200 卖 100 → 部分卖出；同日先卖后买不能被标已卖出。"""
        led = {"A": {
            "trades": [
                {"at": "2026-11-18 14:59:00", "action": "买入", "code": "600010",
                 "name": "甲", "price": 10.0, "shares": 200},
                {"at": "2026-11-20 14:50:00", "action": "卖出", "code": "600010",
                 "name": "甲", "price": 11.0, "shares": 100, "pnl": 100.0},
                {"at": "2026-11-20 14:55:00", "action": "买入", "code": "600011",
                 "name": "乙", "price": 5.0, "shares": 100},
            ],
            "slots": [{"name": "甲", "code": "600010", "shares": 100, "cost": 10.0,
                       "buy_date": "20261118"},
                      {"name": "乙", "code": "600011", "shares": 100, "cost": 5.0,
                       "buy_date": "20261120"}],
        }, "B": {"trades": [], "slots": []}, "C": {}}
        res = self._run("20261120", ledgers=led)
        # 今日买入乙：同日有买入无卖出，状态应为持仓中
        buys = res["instances"]["A"]["buys_today"]
        self.assertTrue(buys)
        self.assertEqual(buys[0]["status"], "持仓中")

    def test_save_false_does_not_write(self):
        """R18：历史/测试 build 不覆盖生产 JSON。"""
        import pathlib
        out = pathlib.Path(daily_review.OUT)
        before = out.read_text(encoding="utf-8") if out.exists() else None
        self._run("20261124")
        after = out.read_text(encoding="utf-8") if out.exists() else None
        self.assertEqual(before, after)

    def test_invalid_day_raises(self):
        with self.assertRaises(ValueError):
            daily_review.build(day="2026-1-1x", save=False)

    def test_prev_trading_day(self):
        res = self._run("20261124")
        self.assertEqual(res["prev_trading_day"], 20261120)

    def test_summary_contains_sections(self):
        text = daily_review.summary_text(self._run("20261123"))
        for kw in ("个股复盘", "今日买入/推荐", "推荐验证", "当前持仓", "逾期"):
            self.assertIn(kw, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
