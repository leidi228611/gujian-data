# -*- coding: utf-8 -*-
"""日 K 线：取最近 N 根（多源容错）。

运行：python examples/kline.py 600519 30
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gujian_data.config import Config
from gujian_data.datasource import DataHub, normalize


def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    hub = DataHub(Config())
    bars = hub.kline(normalize(code), n)
    for b in bars[-5:]:
        print(b.get("date"), b.get("open"), b.get("high"), b.get("low"), b.get("close"), b.get("volume"))


if __name__ == "__main__":
    main()
