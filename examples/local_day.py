# -*- coding: utf-8 -*-
"""读取通达信本地日线（.day，32 字节/条，不复权）。

准备数据：把通达信日线目录（sh/sz/bj/lday/*.day）放到某处，
然后设置环境变量 GUJIAN_TDX_HOME 指向它。

运行：python examples/local_day.py 600519 10
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gujian_data import tdx


def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    home = tdx.home()
    print("TDX_HOME =", home)
    if not home.exists():
        print("未找到本地数据：请设置 GUJIAN_TDX_HOME 指向含 sh/sz/bj 的目录")
        return
    bars = tdx.read_stock(code)
    dates = bars["date"]
    closes = bars["close"]
    import numpy as np
    price_scale = 100.0
    for d, c in list(zip(dates, closes))[-n:]:
        print(int(d), round(float(c) / price_scale, 2))


if __name__ == "__main__":
    main()
