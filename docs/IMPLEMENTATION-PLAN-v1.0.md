# 天枢增强实施计划 v1.0

> 起草日期：2026-05-08
> 状态：草稿，待盟主审批
> 依据：grill_me 四轮追问结果

---

## 一、背景与目标

天枢权衡系统在借鉴 TradingAgents 理念后，从三个方向增强：

| 方向 | 现状 | 目标 |
|------|------|------|
| **Structured Output** | Screen/Review/Decision 三处均用 regex 解析 LLM 输出，不稳定 | 强制 JSON Schema，Pydantic model |
| **辩论机制** | ScreenAgent → ReviewAgent → DecisionAgent 无质疑环节 | 新增 SkepticAgent（Gate 模式） |
| **记忆闭环** | review_evo.py 有写入机制，但 T+N 验证结果从未被填充 | cron 监控持仓池，T+N 后触发回填 |

---

## 二、Structured Output 改造

### 2.1 新建 agents/schemas.py

集中管理所有 Pydantic Schema。

字段设计（已获盟主确认）：

**Schema 1：ScreenAgent 输出**

| 字段 | 类型 | 说明 |
|------|------|------|
| code | string | 6位股票代码 |
| name | string | 股票名称 |
| driver_level | enum S/A/B/C | 宏观驱动等级 |
| driver_reason | string | 驱动原因（≤50字） |
| theme | string | 主题板块 |

**Schema 2：ReviewAgent 输出**

| 字段 | 类型 | 说明 |
|------|------|------|
| code / name | string | 标的 |
| drive_score | int 0-100 | 驱动验证 |
| position_score | int 0-100 | 位置分析 |
| volume_score | int 0-100 | 量能判断 |
| risk_score | int 0-100 | 风险扫描 |
| total_score | int 0-100 | 综合评分 |
| action | enum | upgrade / keep / demote |
| target_pool | string | 目标池 |
| risk_flags | array[string] | 排坑结果 |

**Schema 3：DecisionAgent 输出**

| 字段 | 类型 | 说明 |
|------|------|------|
| code / name | string | 标的 |
| type | enum | primary / backup / defensive |
| buy_type | enum | callback / breakout / dip |
| entry_price | float | 触发买入价 |
| stop_loss | float | 止损价 |
| target_1 / target_2 | float | 目标价 |
| position_size | float 0-0.3 | 仓位 |
| conditions | array[string] | 不做的情况 |
| expiry | string | 失效日期 T+3 |

### 2.2 降级策略（严格模式）

三处统一加 retry 逻辑，Schema 不匹配则重试 3 次，3 次失败则抛异常，整环节标记失败。不回退 regex。

OpenCode Zen / GLM-4-Air 均支持 response_format 参数传递 JSON Schema，无需换 provider。

### 2.3 regex 旧代码处理

删除 review_agent.py 中 6 种正则兜底格式（~50行），删除 decision_agent.py 中提取执行方案的 regex（~40行）。不保留 fallback。

### 2.4 涉及文件改动

| 文件 | 改动 |
|------|------|
| agents/schemas.py | 新建，~130行 Pydantic Model |
| agents/screen_agent.py | 改 call_llm 加 schema |
| agents/review_agent.py | 改 call_llm 加 schema，删 regex 兜底 |
| agents/decision_agent.py | 改 call_llm 加 schema，删 regex |

---

## 三、质疑者 Agent（SkepticAgent）

### 3.1 定位

Gate 模式：SkepticAgent 必须完成，DecisionAgent 才能运行。

```
ScreenAgent → ReviewAgent → [SkepticAgent] → DecisionAgent → 盟主
                                       ↑
                                   必须完成
```

角色：冷静的怀疑者，挑战主流叙事，找出逻辑漏洞，输出质疑清单。

### 3.2 质疑维度

每票必答5个维度：

1. 驱动逻辑是否可验证？有没有数据支撑？
2. 位置分析有没有忽视关键支撑/压力位？
3. 量能是否真实？有无对倒嫌疑？
4. 风险是否被低估？有没有黑天鹅敞口？
5. 方案本身有没有自相矛盾的地方？

severity=high 的质疑，DecisionAgent 必须给出回应。

### 3.3 新建 agents/skeptic_agent.py

复用 BaseAgent 结构，约 250 行，含：

- SYSTEM_PROMPT（角色定义 + 输出格式）
- challenge() 方法（输入：股票列表 + 审查报告 + 市场环境）
- 输出 SkepticResult JSON（含 challenges 数组 + overall_verdict）

### 3.4 管线集成（main.py）

- SkepticAgent 在 ReviewAgent 之后、DecisionAgent 之前调用
- DecisionAgent 的 prompt 注入 skeptic_result（含 high severity 质疑）
- 飞书卡片：最终方案为主，质疑报告以折叠方式附底部

### 3.5 涉及文件改动

| 文件 | 改动 |
|------|------|
| agents/skeptic_agent.py | 新建，~250行 |
| agents/schemas.py | 加 SkepticResult schema |
| main.py | 加 SkepticAgent 调用节点，~30行 |

---

## 四、T+N 记忆闭环

### 4.1 架构

```
DecisionAgent 写 review_evo.md
      ↓
[每5分钟 cron] monitor_holdings.py
      ↓
持仓池 → 查今日收盘 → 是否到 T+N 验证节点？
      ↓
触发 → 回填"实际结果" + 追加"反思"
      ↓
下次同标的进 ScreenAgent → 注入历史摘要
```

### 4.2 新建 scripts/monitor_holdings.py

- 读取持仓池所有记录
- 比对今日日期 vs 决策日 + N 个交易日（N=3 固定）
- 到达节点：查今日收盘 → 计算涨跌幅 → 调用 review_evo 回填
- 未到达：静默跳过

### 4.3 Cron Job

每 5 分钟运行 monitor_holdings.py，no_agent 模式（纯脚本）。

### 4.4 DecisionAgent 历史注入

新增 `_inject_evo_history(code)` 方法，每次 make_decision 时对候选股注入最近 3 条历史决策摘要（日期 + 验证结果 + 反思）。

### 4.5 涉及文件改动

| 文件 | 改动 |
|------|------|
| scripts/monitor_holdings.py | 新建，~120行 |
| agents/review_evo.py | 加 record_verification() + append_reflection()，~40行 |
| agents/decision_agent.py | 加 _inject_evo_history()，prompt 注入，~30行 |
| cronjob | 创建每5分钟 job |

---

## 五、S级操作池（已完成，无需改动）

| 项目 | 状态 |
|------|------|
| 五池管理/S级操作池.json | ✅ 已创建，今日3只标的 |
| agents/pool_manager.py | ✅ POOL_NAMES 已含 S级操作池 |
| agents/decision_agent.py | ✅ _update_s_pool() 已实现 |

---

## 六、实施顺序

```
阶段一（结构层）：schemas.py + monitor_holdings.py 骨架
                  ↓
阶段二（决策核心）：screen/review/decision 三处改 schema
                  ↓
阶段三（辩论机制）：skeptic_agent.py + main.py 集成
                  ↓
阶段四（闭环激活）：cron job + decision_agent 历史注入
                  ↓
阶段五（测试验证）：用今日数据跑全流程
```

---

## 七、预估规模汇总

| 阶段 | 新增文件 | 新增行 | 修改文件 | 修改行 | 净增减 |
|------|---------|-------|---------|-------|-------|
| 结构层 | schemas.py | +130 | - | - | +130 |
| Structured Output | - | - | screen/review/decision | +60, -90 | -30 |
| SkepticAgent | skeptic_agent.py | +250 | schemas.py, main.py | +30 | +280 |
| T+N 闭环 | monitor_holdings.py | +120 | decision_agent, review_evo | +70 | +190 |
| Cron | crontab | +10 | - | - | +10 |
| **合计** | 3个新文件 | **+510行** | 6个文件 | **+70行** | **+580行** |

---

## 八、Open Questions

| 问题 | 处理方式 |
|------|---------|
| 质疑者 LLM 也用 Structured Output？ | 暂用正则，避免 schema 套娃，后续按需升级 |
| T+N 的 N 是固定3还是可配置？ | 固定3，后续可加 config |
| 交易日历怎么算？ | 简化版（加日历日），后续对接 akshare 交易日 |
| Schema 不匹配时通知谁？ | 通知盟主，视为 P0 事故 |
| regex 删除后要不要保留备份？ | 不保留，代码在 git history |

---

*属下等候盟主审批，批准后立即动工。*
