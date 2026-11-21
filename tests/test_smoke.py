# -*- coding: utf-8 -*-
"""冒烟测试：不联网，验证包可导入、配置可实例化、环境变量生效。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gujian_data.config import Config
from gujian_data.datasource import DataHub


class TSmoke(unittest.TestCase):
    def test_imports(self):
        from gujian_data import tdx, utils  # noqa: F401
        self.assertTrue(callable(DataHub))

    def test_config_and_hub(self):
        cfg = Config()
        hub = DataHub(cfg)
        self.assertIn("eastmoney", hub.BUY_ORDER)
        self.assertTrue(len(hub.BUY_ORDER) >= 3)

    def test_tdx_home_env(self):
        from gujian_data import tdx
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("GUJIAN_TDX_HOME")
            os.environ["GUJIAN_TDX_HOME"] = td
            try:
                self.assertEqual(str(tdx.home()), str(Path(td)))
            finally:
                if old is None:
                    os.environ.pop("GUJIAN_TDX_HOME", None)
                else:
                    os.environ["GUJIAN_TDX_HOME"] = old

    def test_useful_helpers(self):
        from gujian_data.utils import to_float
        self.assertEqual(to_float("1,234.5"), 1234.5)
        self.assertIsNone(to_float("--"))


if __name__ == "__main__":
    unittest.main()
