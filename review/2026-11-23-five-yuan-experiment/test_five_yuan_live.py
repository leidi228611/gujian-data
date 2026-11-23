# -*- coding: utf-8 -*-
"""D 研究臂（5元股涨5%链式）单元测试，合成行情，不联网/不碰真实账本。"""
from __future__ import annotations

import json
import pathlib
import tempfile
import time
import unittest
from unittest import mock

from _tools import five_yuan_live as fy


class TFYLive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp.name)
        fy.STATE = self.base / "sim.json"
        fy.STATE.parent.mkdir(parents=True, exist_ok=True)
        fy.STATE.write_text(json.dumps({"start_capital": 20000, "cash": 20000,
                                        "slots": [], "trades": [], "equity": []}),
                            encoding="utf-8")
        self.prices = {"600000": 4.0, "600001": 3.0, "600002": 2.5}
        self._td_patch = mock.patch("stocklens.trading.is_trading_day", return_value={"trading": True})
        self._td_patch.start()
        self._orig_quote = fy._quote
        fy._quote = lambda hub, code: self.prices.get(code)

    def tearDown(self):
        fy._quote = self._orig_quote
        self._td_patch.stop()
        self.tmp.cleanup()

    def _tick(self, now, cands=None):
        return fy.tick(now=now, hub=object(), candidates=cands or ["600000"])

    def test_buy_in_window_and_no_t1_sell(self):
        today = time.strftime("%Y%m%d")
        r = self._tick("14:55")
        self.assertIn("买入", "".join(r["actions"]))
        led = fy._load()
        self.assertEqual(len(led["slots"]), 1)
        # 当日涨到 +6% 也不能卖（T+1）
        self.prices["600000"] = 4.3
        r = self._tick("15:00", ["600000"])
        self.assertEqual(len(fy._load()["slots"]), 1)
        self.assertIn("T+1", "T+1")  # 语义：没有卖出动作
        self.assertFalse(any("卖出" in a for a in r["actions"]))

    def test_sell_at_5pct_and_rebuy_same_tick(self):
        self._tick("14:55", ["600000"])
        led = fy._load()
        led["slots"][0]["buy_date"] = "20260101"  # 非今日，允许卖
        fy._save(led)
        self.prices["600000"] = 4.3  # ≥ 4.2
        r = self._tick("10:00", ["600000", "600001"])
        acts = "".join(r["actions"])
        self.assertIn("卖出", acts)
        # 卖出后同一 tick 应立即买下一只（且不能买回刚卖出的同一只）
        self.assertIn("买入", acts)
        led = fy._load()
        self.assertEqual(len(led["slots"]), 1)
        self.assertNotEqual(led["slots"][0]["code"], "600000")

    def test_price_above_5_not_bought(self):
        self.prices["600000"] = 5.5
        r = self._tick("14:55", ["600000"])
        self.assertFalse(any("买入" in a for a in r["actions"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)