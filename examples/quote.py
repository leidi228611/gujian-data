# -*- coding: utf-8 -*-
"""实时行情：多源容错取一只股票的报价。

运行：python examples/quote.py 600519
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gujian_data.config import Config
from gujian_data.datasource import DataHub, normalize


def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    hub = DataHub(Config())
    q = hub.quote(normalize(code))
    print(f"{code}: {q.get('name')} 最新 {q.get('price')} 涨跌 {q.get('change_pct')}% 来源 {q.get('source')}")


if __name__ == "__main__":
    main()
