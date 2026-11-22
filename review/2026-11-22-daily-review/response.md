# 对 superagent 审阅（issue #73）的处置回复

> 审阅交付：2026-11-22，21,103 字节。基线 `gujian-data@3876721`。
> 本文逐条说明：采纳 / 部分采纳 / 不采纳，以及证据位置（主仓 `stocklens/` 与 `tests/`）。

## 结论总览

- **F1/F2/F3 三条高优先级：全部采纳并修复**（当天推送/归因最危险的三个问题）。
- **F4–F13 中优先级：采纳修复 8 条，部分采纳 2 条，1 条转为文档/后续**。
- **F14–F20 低优先级：采纳 4 条，其余记录为后续**。
- 新增回归测试（R7/R9/R10/R13/R14/R15/R17/R18 等）；主仓全量 **787 项通过**。
- 复现了审阅指出的一个衍生问题：早期旧口径 equity 点（2026-11-18）会把 MDD 算成 -80%+；已加异常点剔除 + 测试。

## 逐条处置

### 高（全采纳）

| 编号 | 处置 | 修复 |
|---|---|---|
| **F1 推荐验证显示买入日收盘** | ✅ 采纳 | `daily_review._review_one` 同时返回 `close`（买入日/查看日）与 `latest_close/latest_ret_pct/latest_date`；`summary_text` 与 `wecom_push` 的「最新」改用 latest，并显示价格日期。测试 `test_prev_validation_uses_latest_close`（R14）。 |
| **F2 None 格式化崩溃、整段静默消失** | ✅ 采纳 | `summary_text` 全部经 `_safe_pct`/`_fmt_shares`；holdings `pnl_pct=None` 显示「—」；无 K 线显示「无当日K线」。测试 `test_no_kline_no_crash`、`test_cost_zero_holdings`（R13/R15）。 |
| **F3 build 无条件写生产 JSON** | ✅ 采纳 | `build(..., save=False)`；写盘改 `tmp + os.replace` 原子替换；API `?date=` 传 `save=False`；测试默认不写盘。测试 `test_save_false_does_not_write`（R18）。 |

### 中（采纳）

| 编号 | 处置 | 修复 |
|---|---|---|
| **F4 已实现收益率与总权益并置** | ✅ 采纳 | 文案拆开：「已实现 X 元」「总权益 Y%（含浮亏）」；`summary_text`/wecom/postmortem 同步；Docstring 说明两者口径。 |
| **F5 合计缺字段** | ✅ 采纳 | `_combined` 补齐 avg_win/avg_loss/profit_factor/expectancy/fees_total/max_drawdown_pct/overdue；测试 `test_combined_fields_present`（R10）。 |
| **F6 利润因子被叫「盈亏比」** | ✅ 采纳 | 摘要与 postmortem 表头统一改为「利润因子」；文档已注明是总盈利/总亏损。 |
| **F7 MDD 基线/合计** | ✅ 采纳 | MDD 起点含本金、允许跌到 0；合计按各实例 equity 按日期合并计算；额外修复早期异常点（见下文）。测试 `test_max_drawdown_counts_start_and_zero`（R7/R8）。 |
| **F8 bars 缓存永不失效/空结果缓存** | ✅ 采纳 | 缓存带 300s TTL；空结果不缓存。 |
| **F9 基准短序列 IndexError / 非 as-of** | ✅ 采纳 | `_benchmark(day=...)` 按 as-of 截断；`len<2` 返回 None；测试 `test_benchmark_short_series_none`、`test_benchmark_as_of_day`（R9）。 |
| **F13 JSON 非原子写** | ✅ 采纳 | 两个模块统一 `tmp + os.replace`。 |
| **F12 卖出状态不按股数** | ✅ 采纳 | `_status_for` 按 slot 股数 + 买入后累计卖出股数判断：持仓中 / 部分卖出 / 已卖出 / 未成交；同日先卖后买不再误标已卖出。测试 `test_partial_sell_status`（R17）。 |
| **F17 API 日期未校验** | ✅ 采纳 | `/api/daily-review` 校验 YYYYMMDD，非法返回 400；`build` 内部再校验。 |
| **F14 节假日 sell_due 近似** | ⚠️ 部分采纳 | 现有 `_sell_due` 仍用工作日近似；已在计划中（用 TDX 交易日历统一），本轮未改，避免影响既有到期口径。 |
| **F10 账本/TDX 读取失败静默当空** | ⚠️ 部分采纳 | 新增 `missing_instances` 标记并在摘要末尾提示「账本读取失败：X（数据不可用，不代表空仓）」；TDX 失败仍返回空列表（有告警但不阻断）。 |

### 低（采纳 / 记录）

- **F11 None 展示**：✅ 摘要不再输出 `None%`/`None`（`_fmt`/`_safe_pct`）；测试 `test_summary_no_none`。
- **F16 JSON 引用声明不符**：✅ 已在回复中更正：调用点均调用函数，`review_stats.json` 仅由 CLI/显式 save 更新。
- **F18 费用/零盈亏口径**：✅ 文档补充：`pnl` 已含费用；`fees_total` 为所有交易费用汇总，二者不重复相加。
- **F19 「去重」注释**：✅ 删除误导注释，`buy_count` 明确为各实例计数之和。
- **F20 文案混合状态**：✅ 改为「无（非交易日/无数据/今日未买入）」，并单列 missing 提示。
- **F15 sample 不完整**：✅ 承认 sample 为节选；本次快照仍保留节选并已在 README 标注。
- **F14 未完成部分**：记录为后续事项。

### 衍生修复（审阅未单列，本轮发现）

**F7-衍生：早期异常 equity 点导致 -80%+ 回撤。**
真实账本 2026-11-18 的 equity 点（7978.93 / 7989.92）是资金口径切换前的旧数据，
纳入 MDD 会得到 -84%～-86%。已加 `_clean_equity_points`：剔除「<50% 本金且之后存在 ≥80% 本金点」的旧点，
并输出 `equity_points_used/excluded` 便于审计；测试 `test_suspicious_early_equity_excluded`。
修复后真实数据：A/B 回撤 -1.59%，合计 -5.12%（都从本金起算）。

## 新增回归测试（对应 R 编号）

`tests/test_review.py`：R7/R8 回撤、R9 基准短序列/as-of、R10 合计字段、R11 空账本无 None、save=False。
`tests/test_daily_review.py`：R13 无当日 K 线、R14 推荐验证用最新、R15 cost=0、R17 部分卖出/同日先卖后买、R18 不写盘、非法日期。

## 未采纳 / 仍存在的残差（如实）

1. **F14 节假日 sell_due**：未改（需统一交易日历，影响面大，排后续）。
2. **F10 TDX 读取失败**：只提示 missing，未区分「TDX 不可用」与「无数据」。
3. **历史 API 日期**：`?date=` 现在 save=False，不会再覆盖 canonical；历史补算仍依赖当前账本数据（不做时点快照）。
4. **缓存 TTL** 是进程内 300s；多进程（服务/MCP）各自缓存，可接受。
5. **「样本不足」提示**：未加强制 `MIN_SAMPLE=20` 常量；推送已拆分「已实现/总权益」，样本量警示仍靠人工判断。

## 结论

- 高优先级 F1/F2/F3 已修，M 级主要项已修；
- 审阅指出的「口径混用、静默丢复盘、覆盖产物」三类问题均已闭环；
- 残差 5 条明确记录，不声称全部修完。
