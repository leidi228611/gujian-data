# -*- coding: utf-8 -*-
"""每日个股复盘单测（2026-11-22）：买入复盘/推荐验证/持仓/逾期。"""
from __future__ import annotations

import unittest
from unittest import mock

from stocklens import daily_review


def _bars() -> dict[int, float]:
    return {
        20261118: 10.0,
        20261119: 10.5,
        20261120: 11.0,
        20261121: 11.5,   # 周六（实际休市，测试用）
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
    def _run(self, day: str = "20261123"):
        dates = [20261118, 20261119, 20261120, 20261124]
        with mock.patch.object(daily_review, "_trading_dates", return_value=dates):
            return daily_review.build(day=day, ledgers=_ledgers(), bars_fn=_bars_fn)

    def test_today_buy_review(self):
        res = self._run("20261124")
        a = res["instances"]["A"]
        # 20261124 无买入（买入在 11-20），今日买入为空
        self.assertEqual(a["buys_today"], [])
        # 上一交易日（11-20）的买入应被验证
        self.assertEqual(len(a["prev_buys"]), 1)
        b = a["prev_buys"][0]
        self.assertEqual(b["name"], "甲")
        self.assertAlmostEqual(b["close_ret_pct"], (11.0 / 10 - 1) * 100, places=1)
        self.assertAlmostEqual(b["next_ret_pct"], (12.0 / 10 - 1) * 100, places=1)

    def test_holdings_and_overdue(self):
        res = self._run("20261123")
        a = res["instances"]["A"]
        hs = {h["code"]: h for h in a["holdings"]}
        self.assertIn("600001", hs)
        self.assertIn("600002", hs)
        self.assertFalse(hs["600001"]["overdue"])
        self.assertTrue(hs["600002"]["overdue"])   # sell_due 20261121 < 20261123

    def test_prev_trading_day(self):
        res = self._run("20261124")
        self.assertEqual(res["prev_trading_day"], 20261120)

    def test_summary_contains_sections(self):
        text = daily_review.summary_text(self._run("20261123"))
        self.assertIn("个股复盘", text)
        self.assertIn("今日买入/推荐", text)
        self.assertIn("推荐验证", text)
        self.assertIn("当前持仓", text)
        self.assertIn("逾期", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
