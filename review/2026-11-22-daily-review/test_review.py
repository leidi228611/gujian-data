# -*- coding: utf-8 -*-
"""复盘统计单测（2026-11-22）：胜率/期望/盈亏比/回撤/逾期/合计。"""
from __future__ import annotations

import unittest

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
        self.assertEqual(a["win_trades"], 2)
        self.assertEqual(a["win_rate_pct"], 66.7)
        self.assertAlmostEqual(a["expectancy"], round((200 - 100 + 300) / 3, 2))
        self.assertAlmostEqual(a["profit_factor"], round(500 / 100, 2))
        self.assertEqual(a["pnl_total"], 400.0)
        self.assertEqual(a["fees_total"], 15.0)
        self.assertEqual(a["today"]["sells"], 1)   # 只有 11-20 那笔算今天
        self.assertEqual(a["today"]["pnl"], 300.0)
        # 逾期未卖：sell_due 20260101 < 20261122
        self.assertEqual(len(a["overdue"]), 1)
        # 回撤：11000 → 10500 = -4.55%
        self.assertAlmostEqual(a["max_drawdown_pct"], -4.55, places=1)

    def test_empty_equity_fallback(self):
        res = review.compute(day="20261120", ledgers=_ledgers(), save=False)
        b = res["instances"]["B"]
        self.assertEqual(b["closed_trades"], 0)
        self.assertIsNone(b["win_rate_pct"])
        self.assertEqual(b["equity"], 5000.0)
        self.assertEqual(b["ret_pct"], 0.0)
        c = res["instances"]["C"]
        self.assertEqual(c["equity"], 0.0)

    def test_combined(self):
        res = review.compute(day="20261120", ledgers=_ledgers(), save=False)
        comb = res["combined"]
        self.assertEqual(comb["closed_trades"], 3)
        self.assertEqual(comb["pnl_total"], 400.0)
        self.assertAlmostEqual(comb["equity"], 10900 + 5000, places=2)

    def test_summary_text(self):
        text = review.summary_text(review.compute(day="20261120", ledgers=_ledgers(), save=False))
        self.assertIn("胜率 66.7%", text)
        self.assertIn("盈亏比 5.0", text)
        self.assertIn("逾期未卖", text)

    def test_save_false_does_not_write(self):
        import pathlib
        out = pathlib.Path(review.OUT)
        before = out.read_text(encoding="utf-8") if out.exists() else None
        review.compute(day="20261120", ledgers=_ledgers(), save=False)
        after = out.read_text(encoding="utf-8") if out.exists() else None
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
