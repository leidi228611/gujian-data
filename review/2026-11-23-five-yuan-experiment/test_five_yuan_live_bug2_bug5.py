# -*- coding: utf-8 -*-
"""补测（BUG-2 / BUG-5 回归）：涨跌停检测 + 账本并发文件锁。

测试对象：22:34:52 最新快照（md5 888fbe82），BUG-2/BUG-5 已由协作方修入。
本文件不修改修复、只锁住行为：
  BUG-2：涨停不买、跌停不卖；无涨跌停数据时 fail-open（当前行为的瑕疵，留档不断言为对）。
  BUG-5：并发 tick 被锁跳过、异常时 finally 释放锁、陈旧锁可回收。

隔离：账本/锁文件指向临时目录，log 置空；is_trading_day 与 _limits/_quote 全部 mock。
不碰 data_d/data/sim.json、不连服务器、不下单。

运行：
  PYTHONPATH=D:/自动炒股 python -X utf8 D:/自动炒股/collab/inbox/test_five_yuan_live_bug2_bug5.py
"""
from __future__ import annotations

import inspect
import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch

from _tools import five_yuan_live as fy


class _LedgerMixin:
    """公共前置：临时账本 + mock 行情/日志/交易日判断。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp.name)
        fy.STATE = self.base / "sim.json"
        fy.LOG = self.base / "live.log"
        fy.STATE.write_text(json.dumps({
            "start_capital": 20000, "cash": 20000,
            "slots": [], "trades": [], "equity": [],
        }), encoding="utf-8")

        self._orig = {
            "quote": fy._quote, "limits": fy._limits,
            "log": fy.log, "tick_impl": fy._tick_impl,
        }
        self.prices = {"600000": 4.0, "600001": 3.0}
        self.limits = {"600000": (4.4, 3.6), "600001": (3.3, 2.7)}
        fy._quote = lambda hub, code: self.prices.get(code)
        fy._limits = lambda hub, code: self.limits.get(code, (None, None))
        fy.log = lambda msg: None

        self._td = patch("stocklens.trading.is_trading_day",
                         return_value={"trading": True})
        self._td.start()

    def tearDown(self):
        self._td.stop()
        for k in ("quote", "limits", "log", "tick_impl"):
            setattr(fy, "_" + k if k != "log" else "log", self._orig[k])
        fy._release_lock()          # 清理本测试遗留的锁文件
        self.tmp.cleanup()


class TestBug2Limits(_LedgerMixin, unittest.TestCase):
    """BUG-2：涨停不买、跌停不卖。"""

    def test_source_has_limit_guard(self):
        """源码护栏：直接读文件（_limits 已被 mock，不能用 inspect.getsource）。"""
        src = pathlib.Path(fy.__file__).read_text(encoding="utf-8")
        self.assertIn('"limit_up"', src, "涨跌停字段 limit_up 读取逻辑缺失")
        self.assertIn('"limit_down"', src, "涨跌停字段 limit_down 读取逻辑缺失")
        self.assertIn("price <= _down * 0.999", src, "跌停卖出拦截缺失")
        self.assertIn("price >= _up * 0.999", src, "涨停买入拦截缺失")

    def test_limit_up_blocks_buy(self):
        """现价触及涨停价时不应买入（up*0.999 缓冲内即拦截）。"""
        self.prices["600000"] = 4.5
        self.limits["600000"] = (4.5, 4.05)      # 4.5 >= 4.5*0.999 → 涨停
        r = fy.tick(now="14:55", hub=object(), candidates=["600000"])
        self.assertNotIn("买入", "".join(r["actions"]), "涨停价不应买入")
        self.assertEqual(fy._load()["slots"], [], "应保持空仓")

    def test_normal_price_can_buy(self):
        """未触及涨停时应正常买入（防止拦截过度）。"""
        self.prices["600000"] = 4.0
        self.limits["600000"] = (4.5, 3.6)       # 4.0 < 4.5*0.999 → 正常
        r = fy.tick(now="14:55", hub=object(), candidates=["600000"])
        self.assertIn("买入", "".join(r["actions"]))
        self.assertEqual(len(fy._load()["slots"]), 1)

    def test_limit_down_blocks_sell(self):
        """现价已达 +5% 但封在跌停板时不应卖出。"""
        # 先买入并绕过 T+1
        fy.tick(now="14:55", hub=object(), candidates=["600000"])
        led = fy._load()
        led["slots"][0]["buy_date"] = "20260101"
        fy._save(led)
        self.prices["600000"] = 4.3              # >= 成本 4.0×1.05=4.2，达卖出线
        self.limits["600000"] = (4.8, 4.5)       # 跌停价 4.5：4.3 <= 4.5*0.999 → 跌停
        r = fy.tick(now="10:00", hub=object(), candidates=[])
        self.assertNotIn("卖出", "".join(r["actions"]), "跌停封板时不应卖出")
        self.assertEqual(len(fy._load()["slots"]), 1, "持仓应保留")

    def test_sell_when_not_limit_down(self):
        """达到 +5% 且未跌停时应正常卖出（防止拦截过度）。"""
        fy.tick(now="14:55", hub=object(), candidates=["600000"])
        led = fy._load()
        led["slots"][0]["buy_date"] = "20260101"
        fy._save(led)
        self.prices["600000"] = 4.3
        self.limits["600000"] = (4.8, 3.6)       # 非跌停
        r = fy.tick(now="10:00", hub=object(), candidates=[])
        self.assertIn("卖出", "".join(r["actions"]))
        self.assertEqual(fy._load()["slots"], [])

    def test_failopen_when_limits_unavailable(self):
        """留档当前瑕疵：_limits 取不到数据时 fail-open，涨停也会买。"""
        self.prices["600000"] = 4.5
        self.limits["600000"] = (None, None)     # 数据源缺字段
        r = fy.tick(now="14:55", hub=object(), candidates=["600000"])
        # 不断言对错，锁住“现状=会买”，便于日后修复时显式更新此测试
        self.assertIn("买入", "".join(r["actions"]),
                      "若此断言失败，说明 fail-open 已被修复，请同步更新本用例与报告")


class TestBug5Lock(_LedgerMixin, unittest.TestCase):
    """BUG-5：账本并发文件锁。"""

    def test_source_has_lock_guard(self):
        """源码护栏：确认 tick 外层有 acquire/release 且 finally 释放。"""
        src = inspect.getsource(fy.tick)
        self.assertIn("_acquire_lock", src)
        self.assertIn("_release_lock", src)
        self.assertIn("finally", src)
        self.assertIn("O_EXCL", inspect.getsource(fy._acquire_lock))

    def test_concurrent_tick_skipped(self):
        """已有进程持锁时，本次 tick 应跳过且不改账本。"""
        self.assertTrue(fy._acquire_lock(), "前置：应拿到锁")
        before = fy._load()
        r = fy.tick(now="14:55", hub=object(), candidates=["600000"])
        self.assertEqual(r.get("skipped"), "locked")
        self.assertEqual(r["actions"], [])
        self.assertEqual(fy._load(), before, "跳过时账本不应变化")

    def test_lock_released_after_normal_tick(self):
        """正常 tick 结束后锁文件应已释放。"""
        r = fy.tick(now="14:55", hub=object(), candidates=["600000"])
        self.assertIn("买入", "".join(r["actions"]))
        self.assertFalse(fy._lock_path().exists(), "正常结束后应释放锁")

    def test_lock_released_on_exception(self):
        """_tick_impl 抛异常时 finally 仍应释放锁。"""
        def boom(**kwargs):
            raise RuntimeError("simulated crash")
        fy._tick_impl = boom
        with self.assertRaises(RuntimeError):
            fy.tick(now="14:55", hub=object(), candidates=["600000"])
        self.assertFalse(fy._lock_path().exists(), "异常路径也必须释放锁")

    def test_stale_lock_reclaimed(self):
        """超过 10 分钟的陈旧锁应能被回收。"""
        p = fy._lock_path()
        p.write_text("99999", encoding="utf-8")
        old = time.time() - 601
        os.utime(p, (old, old))
        self.assertTrue(fy._acquire_lock(), "陈旧锁应可回收")
        self.assertTrue(p.read_text(encoding="utf-8").startswith(str(os.getpid())), "锁 token 应以 pid 开头")


if __name__ == "__main__":
    unittest.main(verbosity=2)
