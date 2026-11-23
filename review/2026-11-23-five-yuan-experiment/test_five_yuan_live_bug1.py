# -*- coding: utf-8 -*-
"""补测（BUG-1 回归）：D 臂卖出后同一 tick 不应买回刚卖出的同一只股票。

规则依据：report-live-D.md 第7行「卖出后同一 tick 立即买下一只 ≤5 元股票」。
本测试把该语义断言为「新的持仓 code != 刚卖出的 code」。

只读/隔离：账本指向临时目录，log 置空，不碰 data_d/data/sim.json 与 _data/five_yuan_live.log。

运行（在任意目录）：
  PYTHONPATH=D:/自动炒股 python -X utf8 D:/自动炒股/collab/inbox/test_five_yuan_live_bug1.py
"""
from __future__ import annotations

import inspect
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from _tools import five_yuan_live as fy


class TestNoRebuySameCodeOnSell(unittest.TestCase):
    def test_source_contains_bug1_fix(self):
        """源码级护栏：确认 sold_codes 排除逻辑仍在（防止修复被回退）。"""
        src = inspect.getsource(fy)
        self.assertIn("sold_codes", src, "BUG-1 修复（排除刚卖出 code）似乎被回退了")
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp.name)
        fy.STATE = self.base / "sim.json"
        fy.LOG = self.base / "live.log"

        fy.STATE.write_text(json.dumps({
            "start_capital": 20000, "cash": 20000,
            "slots": [], "trades": [], "equity": [],
        }), encoding="utf-8")

        self._orig_quote = fy._quote
        self._orig_log = fy.log
        fy.log = lambda msg: None                       # 不写任何日志
        self.prices = {"600000": 4.0, "600001": 3.0}
        fy._quote = lambda hub, code: self.prices.get(code)

    def tearDown(self):
        fy._quote = self._orig_quote
        fy.log = self._orig_log
        self.tmp.cleanup()

    def _buy_then_force_sell(self):
        """公共前置：14:55 买入 600000 → 改买入日为过去 → 现价拉到 +7.5% 触发卖出。"""
        r = fy.tick(now="14:55", hub=object(), candidates=["600000"])
        self.assertIn("买入", "".join(r["actions"]))
        led = fy._load()
        self.assertEqual(len(led["slots"]), 1)
        self.assertEqual(led["slots"][0]["code"], "600000")
        led["slots"][0]["buy_date"] = "20260101"   # 绕过 T+1，使下一 tick 可卖
        fy._save(led)
        self.prices["600000"] = 4.3                # >= 4.0×1.05=4.2，触发卖出

    def test_same_tick_sell_must_not_rebuy_same_code(self):
        """卖出后同 tick 必须换下一只，不能买回刚卖出的 code。"""
        self._buy_then_force_sell()

        # 候选同时含"刚卖出的600000"和"另一只600001"，两只都有有效报价。
        # 旧代码会从 cands[:30] 随机取到 600000 买回；修复后应排除 sold_codes。
        r = fy.tick(now="10:00", hub=object(),
                    candidates=["600000", "600001"])

        actions = "".join(r["actions"])
        self.assertIn("卖出", actions, "应触发卖出")
        self.assertIn("买入", actions, "卖出后同 tick 应买入下一只")

        new_codes = [s["code"] for s in fy._load()["slots"]]
        self.assertNotIn(
            "600000", new_codes,
            f"BUG-1 复现：卖出 600000 后同 tick 又买回同一只（actions={r['actions']}）。"
            "「卖出即换下一只」要求候选排除刚卖出的 code。",
        )
        self.assertEqual(
            new_codes, ["600001"],
            f"应买入另一只 600001，实际持仓={new_codes}",
        )

    def test_sold_code_excluded_even_when_it_is_only_candidate(self):
        """即便候选池只有刚卖出的那只，也不能买回（空仓等待，而非回补）。"""
        self._buy_then_force_sell()

        # 候选池只剩刚卖出的 600000——旧代码会原样买回，修复后应跳过、保持空仓。
        r = fy.tick(now="10:00", hub=object(), candidates=["600000"])

        self.assertIn("卖出", "".join(r["actions"]))
        self.assertNotIn("买入", "".join(r["actions"]),
                         "唯一候选是刚卖出的 code 时不应买入")
        self.assertEqual(fy._load()["slots"], [], "应维持空仓，等待下一只")


if __name__ == "__main__":
    unittest.main(verbosity=2)
