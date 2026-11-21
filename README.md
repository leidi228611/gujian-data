# 股鉴 · 数据工具集（gujian-data）

> 从「股鉴」项目抽出的 A 股数据基础设施：**多源实时行情容错** + **通达信本地日线解析**。
>
> 项目主页：[aileishao.com](https://aileishao.com) ｜ 股鉴（模拟盘）：[aileishao.com/gujian](https://aileishao.com/gujian/)

纯 Python（标准库 + numpy），无第三方行情 SDK，可直接复用。

## 特性

- **多源实时行情容错**：东方财富 / 腾讯 / 同花顺 CLI / 新浪，按顺序自动切换；
  失败源进入冷却，买入/卖出各有独立源顺序（`BUY_ORDER` / `SELL_ORDER`）
- **通达信本地日线**：`.day` 32 字节记录解析、股票/指数、复权因子、全市场面板、健康检查
- **可独立使用**：不依赖任何数据库或服务，`pip install -e .` 即可
- 代码来自实盘前模拟系统的**真实运行代码**（不是 demo）

## 快速开始

```bash
git clone https://github.com/leidi228611/gujian-data.git
cd gujian-data
pip install -e .            # 需要 numpy
```

```python
from gujian_data.config import Config
from gujian_data.datasource import DataHub

hub = DataHub(Config())

# 实时行情（自动选可用源）
q = hub.quote("600519")
print(q["name"], q["price"], q["change_pct"], q["source"])

# 日 K 线
bars = hub.kline("600519", 30)
print(bars[-1])
```

示例脚本：

```bash
python examples/quote.py 600519     # 实时报价（多源容错）
python examples/kline.py 600519 30  # 最近 30 根日 K
python examples/local_day.py 600519 # 读本地通达信日线
```

## 数据源

| 源 | 说明 |
|---|---|
| 东方财富 | 行情快照 / K 线，主源 |
| 腾讯 | 备用源，稳定 |
| 同花顺 | 通过 `hithink-finance` CLI（可选）；设置 `GUJIAN_HITHINK_CLI` 指定命令 |
| 新浪 | 最后兜底 |

源顺序可在 `config.py` 的 `DEFAULTS["datasource"]` 里调整：

```python
"datasource": {"order": ["eastmoney", "tencent", "sina"],
               "buy_order": ["eastmoney", "tencent", "hithink", "sina"], ...}
```

## 通达信本地数据

`.day` 文件格式：每条 32 字节，日期/开/高/低/收/成交额/成交量/保留，价格按 100（股票）或 1000（ETF）缩放。

准备数据（任意目录都行）：

```
<你的目录>/
├─ sh/lday/sh600519.day
├─ sz/lday/sz000001.day
└─ bj/lday/bj920xxx.day
```

设置环境变量后使用：

```bash
set GUJIAN_TDX_HOME=D:\tdx\lday     # Windows
export GUJIAN_TDX_HOME=/data/tdx    # Linux/macOS
```

```python
from gujian_data import tdx

print(tdx.health())                 # 数据是否完整/最新
bars = tdx.read_stock("600519")     # 不复权日线
idx = tdx.index_series("sh000001")  # 指数序列
```

## 目录结构

```
gujian_data/
├─ config.py            # 配置（默认值 + config.json/.env 覆盖）
├─ datasource/          # 多源行情：__init__(DataHub) / eastmoney / tencent / sina / hithink / net / symbols / cache
├─ tdx.py               # 通达信本地日线
└─ utils.py             # 数值/时间小工具
examples/               # 三个最小示例
tests/                  # 冒烟测试（不联网）
```

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py"
```

## 免责声明

- 本项目仅用于**数据获取与研究**，不提供任何投资建议、买卖信号或收益承诺。
- 行情数据来自公开接口，可能存在延迟/缺失/错误；通达信数据依赖你本地数据文件的完整性。
- 依此进行的任何投资决策与交易，风险自负。市场有风险，投资需谨慎。

## 关于

「股鉴」是一个 A 股模拟交易实验：真实行情、真实规则、每日买卖、滑点与盈亏留痕，用来验证策略是不是真的能跑。
更多：<https://aileishao.com>

## License

MIT
