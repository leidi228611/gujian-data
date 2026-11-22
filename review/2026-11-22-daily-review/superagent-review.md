This is by an automated agent, not a human.

# 独立审阅：每日复盘 v2（2026-11-22）

**核对基线**：`gujian-data@3876721034201d5d275a78aea6c032a83faac609` 的 `review/2026-11-22-daily-review`。其 `review.py`（blob `701663ad`）、`daily_review.py`（`e3faf443`）、两个测试（`1ec99a14`/`28f2fc70`）与 `gujian-core` master 的 `stocklens/*`、`tests/*` 逐字节相同。9 项测试在隔离沙箱（替换 config/utils，无 TDX/账本）5+4 全过。下文行号：`review.py`/`daily_review.py` 指公开快照；调用方行号指 `gujian-core` master。

## 一、结论分级

### 必须修（高）

**F1 上一交易日“推荐验证”展示的是买入日收盘，不是最新/今日收盘**
`_review_one` 对 prev 买入传 `day=prev`，`close_today` 取 `<= prev` 的最后一根 K 线（`daily_review.py:104-107`），`build` 在 `185-188` 这样调用；但 `summary_text` 和 `wecom_push` 把 `b['close']` 标成“最新”（`daily_review.py:228-229`、`wecom_push.py:367-369`）。复现：买 11-20@10，11-20 收 11，11-24 收 12，review day=11-24、prev=11-20 → JSON 里 `close=11.0`、`close_ret=+10%`，而 `next_close=latest_close=12.0`、`next_ret=latest_ret=+20%`；摘要/推送打印“最新 11.0（+10.00%）”。测试 `test_daily_review.py:59-62` 断言的正是 11.0，把 bug 固化了。README 第 15 行明确说“买入价 → **最新收盘**”，实现不符。这是每天都会发生的核心验证口径错误，不是边界问题。

**F2 `summary_text` 对 None 格式化会崩溃，调用方静默吞掉整段复盘**
`daily_review.py:214-216` 用 `close_ret_pct:+.2f`；`106-107` 在无 K 线/买入价为 0 时返回 None。`169` 在 `cost=0` 时 `pnl_pct=None`，`241-243` 直接 `:+.2f`。复现（沙箱）：`cost=0` 或 TDX 无该 code → `TypeError: unsupported format string passed to NoneType.__format__`。`postmortem.py:212-218`、`wecom_push.py:358-374` 都是 `except Exception: pass`，结果是“今日买入/推荐验证/持仓（含逾期）”整段从 postmortem 和 15:10 推送里消失，且没有日志/告警。这直接违背“复盘只记录、如实展示”。

**F3 `build()` 无条件写生产 JSON，历史 API/单元测试会覆盖当天产物**
`daily_review.py:197-201` 每次 build 都写 `_data/daily_review.json`，没有 `save=False`（对比 `review.compute(..., save=False)`，`review.py:177-190`）。`server.py:2514-2517` 把用户传入的任意 `date` 传给 build；`test_daily_review.py:47-50` 也调 build。沙箱复现：`build(day="20260101")` 把 canonical 文件日期改成 20260101；跑 4 项 daily 测试后文件被写成 fixture 的 20261124。历史查询/测试会覆盖当天产物；并发时还会互相覆盖（F13）。

### 必须修（中）

**F4 已实现盈亏与总权益收益率并置，容易被读成同一口径**
`review.py:125-126` 的 `pnl_total` 只累加有 pnl 的卖出；`152` 的 `ret_pct` 用 last equity/start。样本：合计 -788.43 元，但 -1.61% 对应的是 **-1832.83 元总权益变动**；-788.43/114000 = **-0.69%**。A 更明显：closed=0、pnl=0，ret=-1.73%（浮亏/费用）。`review.py:211-214`、`postmortem.py:235-236`、`wecom_push.py:382-385` 写成“累计 -788.43 元（-1.61%）”，读者会误以为 -1.61% 是这 -788.43 的收益率。应分别标注“已实现/总权益（含浮亏）”，或不要括注。

**F5 合计缺 claim 里的字段**
`_combined`（`review.py:140-155`）只有 closed/win_rate/pnl/equity/start/ret/today；README 第 10-12 行和设计文档说“每个实例 + 合计”都有平均盈利/亏损、利润因子、单笔期望、费用、最大回撤、逾期。postmortem 合计行（`235-236`）也只显示平仓/胜率/累计。要么补齐 combined，要么把 claim/文档改成“合计仅这些”。

**F6 利润因子被标成“盈亏比”**
`review.py:124` 是 `sum(wins)/abs(sum(losses))`（profit factor）；`205` 摘要写“盈亏比”；`postmortem.py:224` 表头也写“盈亏比”。测试 A：wins 200/300、loss -100 → PF=5.0，而盈亏比（avg win/avg loss）=250/100=2.5；`test_review.py:49/79` 断言 5.0。名称会误导读者把 PF 当盈亏比；改叫“利润因子”，或另算 payoff ratio。

**F7 最大回撤只从 equity 序列算，且跳过 0/负值**
`review.py:57-74`；`65-66` 跳过 `v<=0`，`[100,0]` 得到 0.0 而不是 -100%；`101` 只看实例，combined 没有；`136` 有 `daily_equity_points` 但 sample 没给。样本 A：总收益 -1.73%，MDD 只有 -0.86%，说明 equity 序列不是从 60000 起算（或 MDD 不是从本金起算）；B 的 -788.43 与 -0.56% 也需要 B 的 equity 才能解释。若按本金起算，应把 start 作为第一个峰值；否则文档要写“仅统计已有 equity 点”，并显示点数/区间。

**F8 `_bars_cache` 永不失效，空结果也缓存**
`daily_review.py:24, 27-39`。长驻 Web/MCP 进程里，TDX 日线更新后仍用旧价，直到重启；一次 TDX 读取失败（返回 `{}`）会永久缓存空表。复现：第一次 build 读到 11.0，更新 bars 为 12.0 后第二次 build 仍 11.0。加 TTL/按交易日清空；失败不要缓存。

**F9 基准短序列会 IndexError，且不是 as-of day**
`review.py:158-174`；`170` `recent=max(1,min(10,len-1))`，len=1 时 `closes[-1-1]` → IndexError（不在 try 内）；只有 1 个点时应该返回 None。`_benchmark()` 默认取 TDX 最新，不按传入的 day；历史回放/补算会用到未来数据。样本 -3.16% 没有提供 11 个收盘价，无法手算。

**F10 账本/TDX 读取失败被静默当空**
`review.py:32-41` 每个实例异常 → `{}`；`daily_review.py:42-50` 同样；`_trading_dates`（`53-58`）异常 → `[]`。combined 的 start/equity 会少掉该实例（ret 被扭曲），摘要显示“暂无/空仓/上一交易日（—）”，读者无法区分“没数据”和“空仓”。至少返回 missing 标记并在文本里提示。

**F12 卖出状态只按 code + 日期比较，不按 shares/批次**
`daily_review.py:117-124`：同日先卖后买会被标“已卖出”（沙箱复现）；同 code 两笔买入只卖了一笔、仍有 slot 时两笔都“持仓中”；部分卖出也不区分。建议按 shares 累计或明确“部分卖出”。

**F13 JSON 写入非原子，多个写者**
`daily_review.py:197-201`、`review.py:185-190` 直接 `write_text`；postmortem、wecom、API、MCP 都会触发 build（F16），并发时可能读到半截文件/后写覆盖先写。用 tmp + `os.replace`；daily_review 加 save 开关。

**F15 `sample-output.txt` 不是代码的完整“真实输出”**
review 侧顶层是 A/B（代码是 `instances`，`review.py:182-184`），缺 at/C/daily_equity_points/多数字段；daily 侧同样顶层 A/B（代码 `instances`，`daily_review.py:149-150,189-192`），缺 buy_count/prev_buy_count。若只是节选，请标注；否则“真实输出”不成立。因缺 equity 序列和 11 个指数收盘，MDD 与 -3.16% 都无法独立手算，只能核对一致性。

### 建议（低）

- **F11 None 显示**：`review.py:205` 在无亏损时 PF=None，摘要打印“盈亏比 None”；`211-214` 在 0 平仓时打印“胜率 None%”；postmortem 合计行同样。改成“—/∞/样本不足”。
- **F14 `_sell_due` 用工作日近似**：`market_view.py:194-207`；daily_review 在 slot 无 sell_due 时回退到它（`daily_review.py:170-176`）。节假日附近到期日可能落在非交易日，overdue 偏早/偏晚。建议统一用 TDX 交易日序列。
- **F16 JSON 引用声明不符**：claim 说 JSON 被 postmortem/微信/API/MCP 引用；实际这些点都是调用函数：`postmortem.py:213-214, 221-222`、`wecom_push.py:360-361, 377-378`、`server.py:2514-2517`、`_tools_gujian_mcp.py:118-120`。`review.compute(save=False)` 不会刷新 `review_stats.json`；只有 CLI `review.main()` 会写。若外部依赖该文件，会读到旧数据。
- **F17 API 日期未校验**：`daily_review.py:144` `int(day)`；`server.py:2516` 直接传 `q.get("date")`。`?date=2026-11-22` → ValueError → 500；建议 400 + 明确格式；历史日期还会覆盖 canonical（F3）。
- **F18 费用/零盈亏口径未定义**：`review.py:80-83` 把 pnl=0 的卖出计入 closed（不算 win/loss），`121` 胜率分母含它；`83` fees 汇总所有买卖（不只平仓）。样本 A closed=0 但 fees=15.25。若 pnl 已含 fee，`pnl_total` 与 fees 的关系要写清。
- **F19 “去重”注释未实现**：`daily_review.py:194-196` 注释说“去重同一个 code 在 A/B 都买的情况”，但 `buy_count` 只是 sum，没有去重；要么实现，要么删注释。
- **F20 文案混合状态**：`daily_review.py:219` “无（非交易日或今日未买入）”把两种状态合并；周末/数据缺失时无法判断。建议区分“非交易日/无数据/今日未买入”。

### 不成立 / 已核对

- **B 的参考数字自洽**：closed=2、win_rate=0.0%、avg_loss=expectancy=-394.21、PF=0.0、pnl=-788.43 全部正确；-788.43/2=-394.215→-394.21 ✓。
- **合计收益率正确**：112167.17/114000-1=-1.6077%→-1.61 ✓；A：58960.75/60000-1=-1.7321%→-1.73 ✓。
- **9 项测试全过**；review 侧 `save=False` 确实不写（`test_save_false_does_not_write` 通过）。
- **11-22 是周日**，prev=11-20 正确；`overdue` 用 due<day，样本 11-23/11-25 在 11-22 不逾期，正确。
- **基准公式方向正确**（10 个交易日区间），但数值无法用给定 sample 复核。
- **“2 笔胜率 0% 算错”不成立**：0/2=0.0% 计算正确；问题是展示/样本量（Q5）。
- **“B 的 -788.43 与 MDD -0.56% 一定矛盾”不成立**：MDD 是峰值到谷值，可能有浮盈/未实现，需 B 的 equity 才能判断；但 A 的 -1.73% vs -0.86% 说明序列不以本金起算，需澄清。
- **“重复读账本+TDX 性能问题”在当前规模不成立**：15:10 一次推送只有 3 个 JSON + 少量 .day 文件；真正的问题是 F8 缓存陈旧，不是速度。
- **“样本<20 不改规则”在代码里没有被违反**：没有任何改规则路径；风险在推送表达（Q5）。

## 二、Q1 指标口径

| 指标 | 实现 | 与说明是否一致 |
|---|---|---|
| 平仓笔数/胜率 | `_closed_trades`：卖出且 pnl 非 None（`review.py:44-46`）；胜率 wins/closed（`121`） | 基本一致；pnl=0 计入 closed 但不算 win/loss，胜率分母含它，需写明 |
| 平均盈利/亏损 | `122-123` | 一致；合计没有这两个字段（F5） |
| 利润因子 | `124` 总盈利/总亏损 | 公式一致；名称在摘要/表头写成“盈亏比”（F6）；无亏损时 None |
| 单笔期望 | `125` mean(pnls) | 一致；B=-394.21 ✓ |
| 最大回撤 | `57-74` equity 峰谷 | 算法一致但基线/0 值/合计有问题（F7） |
| 累计盈亏/收益率/费用 | pnl 只算已实现（`126`）；ret 用总权益（`152`）；fees 算所有交易（`83`） | 三者不是同一口径，展示时并置会误导（F4）；建议拆开标注 |
| 今日买卖/盈亏 | `104-107` | 一致；只统计当日 `at` 前缀 |
| 逾期未卖 | `110-115` due<day | 一致；依赖 sell_due 质量（F14） |
| 基准 | `158-174` 上证最近 10 个交易日 | 公式方向一致；短序列崩溃、非 as-of（F9） |

**手算复核（至少 2 项）**

1. **B 期望/平均亏损**：`-788.43 / 2 = -394.215 → -394.21` ✓；`win=0/2=0.0%` ✓；`PF=0/788.43=0.0` ✓（单笔拆分不影响均值）。
2. **合计收益率**：`112167.17 / 114000 - 1 = -1.6077% → -1.61%` ✓；A：`58960.75 / 60000 - 1 = -1.7321% → -1.73%` ✓。
3. **但 -788.43 与 -1.61% 对不上**：`-788.43 / 114000 = -0.69%`；`-1.61%` 对应总权益 `112167.17 - 114000 = -1832.83`。两者差 `-1044.40`（浮亏/费用等）。所以“累计 -788.43 元（-1.61%）”不是同一个收益率，必须拆开标注。
4. **MDD 无法用 sample 手算**：sample 没有 A/B 的 equity 数组；且 A 的总收益 -1.73% 大于报告 MDD -0.86%——只有当 equity 序列第一个点低于 60000（序列不从本金开始）时才可能。请提供 equity 序列和 `daily_equity_points` 再核。
5. **基准 -3.16% 无法用 sample 手算**：只给了最新 close 和 10 日收益，没给 11 个收盘价；公式是 `closes[-1]/closes[-11]-1`，方向正确。

## 三、Q2 日期与匹配

- **非交易日**：`prev_trading_day` 用 TDX 指数日期，11-22（周日）→11-20 正确；`next_trading_day` 在没有未来数据时为 None，正常。问题是 TDX 读取失败时静默 `[]`（F10），摘要显示“上一交易日（—）推荐验证：无”，像“没有推荐”而不是“数据不可用”。
- **今日无 K 线**：`_latest_close(bars, day)` 会回退到最近一根旧 K 线（`daily_review.py:81-92`），摘要仍标“收盘”，不显示 `close_date`；收益率把今天的买入价和旧收盘混在一起。若完全无 bars → `close=None`、`close_ret=None` → F2 崩溃。应在无当日 K 线时显示“无 K 线/停牌”，不要回退充数。
- **停牌**：没有“最后一根 K 线过旧”的检查；停牌/长期无成交会用旧价算浮盈亏，且不标记。建议在 holdings 里输出 `last_date` 并标注“价格日期/停牌”。
- **prev/next**：用 TDX 交易日，数据新鲜时正确；`next_ret` 只有下一交易日 K 线存在时才有值（通常收盘后当天数据未更新则为 None），这是数据时序问题，不应被当成“没有下一日”。
- **卖出匹配/status**：见 F12。`slot` 只按 code 找，`sold` 只按 code+日期字符串比较，不核对 shares/批次；同日先卖后买、两笔买入只卖一笔、部分卖出都会误判。
- **收益率一致性**：今日买入用 `latest_close(<=day)`；上一交易日买入的 `close` 是买入日收盘，`latest_close/next_close` 是更新价；`summary_text` 和 wecom 却用 `close` 当“最新”（F1）。三套口径必须统一并带日期。
- **买入日不在 TDX**：`_review_one` 的 close/latest 都会 None（若 code 完全无数据）→ F2 崩溃；holdings 会回退到 `s.last`/cost，due 回退 `_sell_due`，buy_date 不在 seq 时返回 ""。

## 四、Q3 集成风险

- **异常影响主流程**：postmortem/wecom 都用 `except Exception: pass`（`postmortem.py:212-218, 220-246`；`wecom_push.py:358-387`），主流程不会挂，但失败不可见；F2 一旦触发，整段复盘直接消失。API 的 `_dr.build()` 没有本地兜底，非法日期/坏数据会 500；MCP 会返回 `isError`（`_tools_gujian_mcp.py:118-126`），相对可见。建议记录 warning/在文本里留“复盘不可用”标记，而不是空 `pass`。
- **重复读/性能**：postmortem 已经读过账本，daily_review 再读 3 份、review 再读 3 份；`_trading_dates`/`_benchmark` 各读一次指数；每个持仓/买入读一次 `.day`。当前 3 个实例、少量持仓，15:10 一次推送可忽略；真正的问题是 F8 的进程级缓存永不刷新，以及 API 每次请求都重算。建议按“一次运行”缓存，并给 `_bars` 加 TTL。
- **并发写**：F3/F13。多个入口（postmortem、wecom、API、MCP、CLI）都会写同一个 `daily_review.json`；`write_text` 非原子，历史 API 可覆盖当天。用 tmp+`os.replace`，并让 `build(save=False)` 成为默认/测试默认。
- **JSON claim**：F16。四个调用点实际都调用函数而不是读 JSON；`review_stats.json` 在 postmortem/wecom 中 `save=False` 不会更新，只有 CLI 会写。若外部依赖这些 JSON，需明确生成时机和读取方。

## 五、Q4 测试缺口

**现有 9 项覆盖**：
- `test_review.py`：指标（3 笔、含 0 胜率/PF/回撤/逾期）、空 equity 兜底、合计、摘要字符串、save=False 不覆盖。
- `test_daily_review.py`：上一交易日买入（断言的是买入日 close，不是 latest）、持仓/逾期、prev_trading_day、摘要小节存在。

**主要缺口**：
- 断言多为“字符串存在”，不是数值正确（`test_summary_contains_sections`、`test_summary_text` 的 assertIn）；`test_save_false` 只覆盖 review；daily 测试反而写生产 JSON（F3）。
- 没有负样本/边界：0 平仓、全赢（PF=None）、pnl=0、费用口径、MDD 归零/不从本金起算、基准短序列/失败、TDX 失败、无当日 K 线、停牌、cost=0、非法日期、部分卖出/同日卖买、缓存 TTL、并发/原子写、API 契约。
- `test_today_buy_review` 名字与内容不符：它只测“今日无买入 + 上一交易日买入”，没有测今日买入主路径。
- 没有用 sample 数字做回归（F15 的 sample 也缺字段）。

**最小回归集（建议 20 条；❌ 表示当前会失败/缺口）**

| # | 场景 | 期望 |
|---|---|---|
| R1 | 三个空账本 | 不崩溃；win_rate/PF/expectancy=None；摘要不出现“None%”/“None” |
| R2 | B 两笔全亏，合计 -788.43 | closed=2、win=0.0、avg_loss=expectancy=-394.21、PF=0.0 |
| R3 | 全赢 +100/+300 | PF=None/∞ 有明确展示；摘要不出现“None” |
| R4 | pnl=0 的卖出 | 计入 closed，不计 win/loss；胜率分母口径有断言/文档 |
| R5 | 买入+卖出都带 fee | fees_total 口径明确；若 pnl 已含 fee，断言不重复扣 |
| R6 | equity 100→110→99→105 | MDD=-10.0%，区间为峰值日→谷值日 |
| R7 | equity 100→0 | MDD=-100% 或明确“数据异常”，不能是 0.0 ❌ |
| R8 | start=60000，equity 从 59500 起 | 明确 MDD 是否从本金起算；sample A 场景要能解释 -1.73% vs -0.86% ❌ |
| R9 | 基准 11 个收盘；仅 1 个收盘；tdx 异常 | 10 日收益正确；1 个点返回 None 不 IndexError；异常→None ❌ |
| R10 | 合计字段契约 | `combined` 含 claim 的 avg/PF/expectancy/fees/MDD/overdue，或文档改口 ❌ |
| R11 | 一个实例账本读取失败 | 不静默当 0；combined 标记 missing；摘要提示数据不可用 |
| R12 | 20261122（周日） | prev=20261120；摘要区分“非交易日/无数据/未买入” |
| R13 | 今日买入但 TDX 无当日 K 线 | close=None/close_date=旧日期，摘要“无 K 线”，不能把旧收盘当今日 ❌ |
| R14 | 买 10、D0 收 11、D1 收 12，review D1 | 推荐验证显示 latest/next=12（+20%），不是买入日 11 ❌ |
| R15 | 持仓 cost=0 / 无 bars | 不崩溃；显示“—”；holdings 不因格式化 None 丢失 ❌ |
| R16 | due==day / due<day / due 为空 / 节假日 due | due==day 不逾期；due<day 逾期；空不逾期；节假日不早报 |
| R17 | 同日先卖后买；两笔买入卖一笔；部分卖出 | 状态分别为“持仓中/部分卖出/持仓中”，不能全标“已卖出” ❌ |
| R18 | `build(day=历史日期)`；跑测试 | 不覆盖 canonical；加 `save=False`；测试后文件内容不变 ❌ |
| R19 | 同一 code 次日 bars 更新 | 第二次 build 用新价；空结果不永久缓存 ❌ |
| R20 | postmortem/wecom 中 build 抛错 | 主流程仍生成，且有可见的“复盘不可用”标记/日志，不是静默消失 |

## 六、Q5 纪律风险

- **样本 2 笔就下结论**：推送“累计平仓 2 笔 · 胜率 0.0% · 累计 -788.43 元（-1.61%）”把 n=2、0% 和一个混合口径的亏损放在一行；postmortem 表虽能看到 2 笔，但没有“样本不足/仅记录”提示。代码里没有 <20 的门槛或 `sample_note`，纪律只存在于文档。建议：显示“0/2（样本不足，仅记录）”；n<20 时胜率/PF 标注“参考值”或直接隐藏；“累计 -788.43（已实现）”与“权益 -1.61%（含浮亏）”分开。
- **复盘数据被用于改规则**：代码里没有自动改规则的路径，这一条目前没有被违反；但 F3（历史 API/测试覆盖产物）、F2（静默丢复盘）、F15（sample 节选）都会削弱“如实记录”的可信度。建议把 `MIN_SAMPLE=20` 做成常量/契约测试，任何规则变更工具先检查样本数。
- **“盈亏比 0.0”**：B 的 PF=0.0 是“无盈利/有亏损”的数学结果，不是“盈亏比”；在 n=2 时更不应作为结论。F6 改名/F4 拆口径能降低误导。

## 七、需要补的材料

1. **完整未删节的** `review_stats.json` 和 `daily_review.json`（含 `instances`、C、`at`、`daily_equity_points`、所有字段），或明确标注 sample 是节选。
2. **A/B 的 equity 数组 + start_capital + trades**（脱敏 fixture 即可），用于手算 MDD、合计收益率和 B 的 -0.56%。
3. **上证 sh000001 最近 11 个交易日收盘价**，用于核对 -3.16%。
4. **15:10 微信推送原文 / postmortem 原文**（含实际显示），确认 F4/F1 在真实文本中的表现。
5. 若想核对 fees 与 pnl 是否重复扣，提供一笔带 fee 的卖出记录及 pnl 定义。

---

**优先级建议**：先修 F1（每天误导）、F2（整段消失）、F3（覆盖产物），再修 F4/F5/F6/F7/F8。修完 F1/F2/F3 后，用 R13/R14/R15/R18/R19 做回归即可覆盖最危险的路径。