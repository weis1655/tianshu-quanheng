# 子任务 C: 业务流程全链路审计报告

> **审计日期**: 2026-07-11
> **审计范围**: 天枢权衡 v6.2 — news→market→screen→review→skeptic→decision→pool 全链路
> **工作目录**: `/home/seven/hermes-data/tianshu-quanheng`

---

## 1. 全链路流程图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          天枢权衡 全链路流程                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐          │
│  │ 06:20   │    │ 07:10    │    │          │    │           │          │
│  │ News    │───▶│ Screen   │───▶│ Review   │───▶│ Skeptic   │          │
│  │ Agent   │    │ Agent    │    │ Agent    │    │ Agent     │          │
│  └────┬────┘    └────┬─────┘    └────┬─────┘    └─────┬─────┘          │
│       │              │               │               │                │
│       ▼              ▼               ▼               ▼                │
│  ┌─────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐         │
│  │ 宏观前置 │    │ 快筛候选  │    │ 重点观察  │    │ 质疑裁决  │         │
│  │ 分析报告 │    │ 池       │    │ 池更新    │    │ JSON输出  │         │
│  └─────────┘    └──────────┘    └──────────┘    └───────────┘         │
│                                                          │             │
│                                                          ▼             │
│                                              ┌──────────────────┐      │
│                                              │  Decision Agent  │      │
│                                              │  (二审制Gate)     │      │
│                                              └────────┬─────────┘      │
│                                                       │                │
│                                                       ▼                │
│                                              ┌──────────────────┐      │
│                                              │  PoolUpdater     │      │
│                                              │  + QualityGate   │      │
│                                              └────────┬─────────┘      │
│                                                       │                │
│                                                       ▼                │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      五 池 管 理                                │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────┐  ┌──────┐  ┌────────┐ │   │
│  │  │ 快筛候选池 │  │ 重点观察池 │  │S级池 │  │边缘池│  │ 持仓池 │ │   │
│  │  │ cap=20    │  │ cap=20   │  │cap=3 │  │cap=30│  │ 无上限 │ │   │
│  │  └──────────┘  └──────────┘  └──────┘  └──────┘  └────────┘ │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                         │         │         │                          │
│                         ▼         ▼         ▼                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │       后置处理（每轮 full_cycle 末尾）                          │   │
│  │  • 边缘池清理（过期标的移除）                                    │   │
│  │  • 全池低分扫描降级（sweep_downgrade）                           │   │
│  │  • 准确率模式刷新                                              │   │
│  │  • MemPalace 保存                                               │   │
│  │  • 五池健康审计                                                │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.1 阶段执行时序 (main.py)

| 阶段 | 触发条件 | LLM调用 | 级联终止 | 输入 | 输出 |
|------|---------|---------|---------|------|------|
| **news_only** | 06:20 或 CLI `news` | 1次 | 否 | 新闻联播内容 | 宏观前置分析.md |
| **market** | 工作日（full_cycle内嵌） | 0次 | 否 | — | 实时行情数据 |
| **screen** | 07:10 或 CLI `screen` | 1次 | ✅ 失败终止后续 | news报告 | 快筛报告 + 候选池更新 |
| **review** | screen成功后 | 1次 | ✅ 失败终止后续 | 快筛报告 | 审查报告 + 池更新(升级/降级) |
| **skeptic** | review成功后（弱市简化绕过） | 1次（弱市0次） | 否（仅记录） | 审查报告+重点观察池 | 质疑审查报告+裁决JSON |
| **decision** | skeptic完成后 | 1次 | 否 | 所有报告+池状态 | 决策报告 + S池更新 |
| **ts** | CLI独立触发 | 0次 | — | — | 时间序列分析报告 |

### 1.2 边界与旁路

- **非交易日**: 仅执行 news_only，跳过 market→screen→review→skeptic→decision 全链
- **周末模式**: news_only + 池维护（候选池过期清理 + 边缘池清理 + 全池降级扫描）
- **弱市（震荡偏弱/偏空）**: 跳过 SkepticAgent LLM 调用，使用纯规则简化审查（风险信号检测）
- **长假后恢复（gap>3天）**: 强制全量池刷新 + 跳过 Skeptic
- **熔断器**: 每阶段前检查 `check_circuit_breaker()`，熔断时跳过该阶段
- **MemPalace**: 唤醒注入跨天记忆 → 运行 → 保存执行摘要

---

## 2. 数据流断连点分析

### 断连点 FC-01: 决策报告评分 → pool_updater 提取 → S池写入

**描述**: `PoolUpdater.update_s_pool()` 从决策报告用正则提取评分（`_extract_score()`），而非直接接收 DecisionAgent 结构化评分。

**证据**:
```python
# pool_updater.py L82-87
score = scored_map.get(code, 0) or self._extract_score(name, code, decision_result)
s = {
    "代码": code, "名称": name,
    "综合评分": score,  # ← 二次提取，非结构化传递
    ...
}
```

**风险**: 
- 正则提取可能失败（格式变化），fallback 到 0 分
- `scored_map` 依赖 `scored_stocks` 参数，若调用方未传则回退正则
- 评分精度丢失（正则提取分数 vs 原始 `int` 分数）

**严重度**: ⚠️ 中 — 有兜底但不精确

### 断连点 FC-02: 审查报告 → Decision Agent 评分解析

**描述**: `DecisionAgent._extract_scores()` 从审查报告 Markdown 中用正则提取评分（`_load_scores_from_report`），而非接收 ReviewAgent 结构化输出。

**证据**: `decision_agent.py L366`
```python
scored_stocks = self._extract_scores(review_report)
```

**风险**: 
- LLM 输出格式变化导致正则失效 → 0 分
- 多头评分（LLM综合分 + ML评分 + 因子加分 + 市场通缩调整）需要在报告中有序呈现供决策层解析

**严重度**: ⚠️ 中 — 当前依赖 LLM格式约束，无结构化 schema 桥接

### 断连点 FC-03: Skeptic 裁决 → Decision Agent 阻塞拦截

**描述**: SkepticAgent 写两个文件（质疑报告.md + 裁决JSON），DecisionAgent 分别读取。裁决JSON是结构化数据，但阻断逻辑依赖 JSON 中的 `blocked` 列表。

**证据**: `decision_agent.py L169-173`
```python
verdict_data = self.safe_read_json(verdict_file, {})
blocked_list = verdict_data.get("blocked", [])
blocked_codes = {s.get("code", "") for s in blocked_list}
```

**风险**: 
- 裁决JSON不存在 → 降级为"质疑缺失"，不阻断（所有标的通过）
- 弱市模式：简化审查直接把裁决写入准确位置，但 Key 格式可能不同
- 弱市裁决JSON中 `blocked` 列表包含 `code/name/reason` 但无 `block_reason` 字段，可能导致首次 high 豁免逻辑无法正确工作

**严重度**: ⚠️ 中 — 弱市简化审查缺少首次豁免逻辑

### 断连点 FC-04: S级操作池 T+1 过期回流

**描述**: S 池过期标的需回流到重点观察池（或边缘池）。T+1 表现数据从闭环追踪 JSON 读取，路径依赖强。

**证据**: `decision_agent.py L214-216`
```python
t1_path = self.root / "data" / "闭环追踪" / f"{yesterday}_闭环追踪.json"
```

**风险**:
- 闭环追踪文件不存在 → 回流不带上 T+1 表现（保守处理，但缺乏是否触发止损的信息）
- 文件路径硬编码，`yesterday` 跨周末/节假日时可能不存在对应文件

**严重度**: ✅ 可控 — 有安全降级处理

### 断连点 FC-05: 快筛→审查 双盲断连

**描述**: ReviewAgent 遵循双盲原则，不看快筛阶段的推荐理由，只看代码和名称。但候选池 JSON 中存储的 `核心逻辑` 字段不向下传递。

**证据**: `review_agent.py` 设计原则
```
此Agent不知道快筛阶段推荐的理由（只看代码和名称）
```

**风险**: 
- S 级驱动的标的在审查时无法获得驱动上下文，可能依赖 LLM 自行从公开信息推断
- 审查报告中缺失驱动级别上下文可能影响评分准确性

**严重度**: ⚠️ 低 — 属有意为之的双盲设计，但弱化了 S 级驱动筛选的收益

---

## 3. 极端场景处理评估

### 3.1 创业板跌 > 3% 时的系统行为

| 机制 | 行为 | 代码位置 |
|------|------|---------|
| **反馈闭环熔断** | `FeedbackLoop.check_market_circuit()` 检测上证<-3%，标记熔断触发 | `feedback_loop.py L137-144` |
| **弱市简化审查** | 震荡偏弱/偏空时，全池跳过 SkepticAgent LLM，改为纯规则审查 | `main.py L856-1058` |
| **市场状态降级** | 创业板在沪深300 vs MA20 判定中影响偏空判定，触发动态评分阈值提升 | `skeptic_agent.py L438-469` |
| **动态准入阈值** | 偏空→≥85分才准入（QualityGate），震荡偏弱→≥80分 | `thresholds.py L49-55` |
| **仓位限制** | 偏空/震荡偏弱：单票≤3%，总仓位≤10% | `thresholds.py L182-184` |
| **S池容量** | 偏空→S池容量收缩到1只 | `pool_updater.py L198-210` |

**评估**: ✅ 多层次应对，充分。

**潜在缺陷**: 
- 熔断检测仅检查上证<-3%，未覆盖创业板单独暴跌（>3%）的场景
- 创业板跌>3%但上证跌幅<3%时，熔断器不会触发

### 3.2 LLM 退化检测与降级路径

| 层 | 降级路径 | 描述 |
|----|---------|------|
| **每阶段熔断器** | `check_circuit_breaker(phase)` | LLM 调用失败 → `record_failure()` → 连续失败达阈值 → 熔断打开 → 60s 后尝试半开 |
| **弱市简化审查** | 纯规则替代 LLM | Skeptic 在弱市下完全用规则检测风险信号，零 LLM 依赖 |
| **指数退避重试** | BaseAgent.call_llm() | 3次重试 + 指数退避 + 抖动 |
| **报告降级** | 写入占位报告 | SkepticAgent 异常时写入占位报告，不阻塞 downstream |
| **JSON安全降级** | 多处 `safe_read_json` | 读取失败返回空 dict，不影响后续 |
| **行情获取降级** | 降级到新浪接口 | 东方财富失败 → 新浪 fallback（screen_agent 技术面补位） |

**评估**: ✅ LLM 退化应对充分，多个降级路径相互独立。

**潜在缺陷**: 
- 无全局 LLM 健康探测（如定期发送探针检测模型响应）
- 熔断器状态不持久化到磁盘（进程重启后重置）

### 3.3 数据源失效的 Fallback 链

| 数据源 | 主路径 | Fallback 1 | Fallback 2 | 最终降级 |
|--------|--------|-----------|-----------|---------|
| **新闻** | 实时抓取 | 复用本地文件(06:20 cron生成) | 智能截断 | news failure → 级联终止 |
| **行情** | 东方财富 API | 新浪财经 API | 安全降级返回空 | 若实时行情缺失 → DecisionAgent 终止 |
| **候选池** | JSON 文件 | - | - | 空池返回 error |
| **审查报告** | json 解析 | 正则提取 | 返回空 dict | 决策层视为"缺失" |

**评估**: ✅ 行情和新闻有可靠 fallback；池文件单点依赖但有异常兜底。

---

## 4. 边界与异常场景分析

### 4.1 S池已满（cap=3）时新标的处理

**机制**:
1. `PoolUpdater.update_s_pool()` 合并旧池标的（仅保留纳入日期为今日的）→ `merged[:3]`
2. `PoolManager.save_pool()` → 超出容量时截断（S池按纳入日期排序，保留最新的3只）

**行为**: 最新 3 只主推标的保留，超出的标的被静默丢弃。

**风险**: 
- **数据丢失**: 第4+只主推标的被截断但未被记录到历史或通知
- **替换逻辑**: 按日期保留最新3只，不区分优先级、驱动级别或评分

**严重度**: ⚠️ 中 — S池 cap=3 是明确设计，但超出时无通知

### 4.2 边缘池已满（cap=30）时新降级标的处理

**机制**: `PoolManager.add_stock()` → `stocks[-max_stocks:]` → 移除最旧的。

**风险**: 移除的旧标的不被记录到任何地方，但没有暂停新标的进入。

**严重度**: ⚠️ 低 — 边缘池容量 30 只足够大，旧标的本就该淘汰

### 4.3 评分=0 或 None 的处理

| 场景 | 处理 | 位置 |
|------|------|------|
| **综合分=None** | `sort_key` 返回 -1（排最后） | `pool_manager.py L196-204` |
| **综合分=0** | `score_to_level(0)` → "D级(淘汰)" | `thresholds.py L190-207` |
| **综合分=0 且入池≥3天** | sweep_downgrade 强制降级边缘池 | `sweep_downgrade.py L61-69` |
| **PoolUpdater 提取评分失败** | `_extract_score()` 返回 0 | `pool_updater.py L278-297` |
| **审查阶段评分转换异常** | `try: float(s.get("综合分")) except -> 0` 或 "?" | 多处 |

**评估**: ✅ 全覆盖处理，None 和 0 均有兜底。

### 4.4 股票代码格式异常

| 异常 | 处理 | 位置 |
|------|------|------|
| **非6位数字** | 正则验证 `\d{6}` 过滤 | 多处 `re.findall` |
| **带前缀** | `validate_stock_codes()` 验证（网络查询） | `screen_agent.py L444-451` |
| **空代码** | `add_stock()` 返回 False | `pool_manager.py L228-232` |
| **跨字段名** | `stock.get("股票代码") or stock.get("代码")` | 全局模式 |
| **市场前缀** | `to_api()` 自动添加 sh/sz 前缀 | `market_agent.py` |

**评估**: ✅ 多字段兼容 + 正则验证双重保障。

---

## 5. 关键正确性校验

### 5.1 决策方案生成 → 硬规则校验 → SkepticGate → 执行计划

```
DecisionAgent._run_impl()
├── 1. S池 T+1过期清理 + 回流
├── 2. 读取审查报告 + 提取评分 (scored_stocks)
├── 3. 加载Skeptic上下文 (二审制Gate)
│   ├── 裁决JSON → blocked_codes
│   └── 质疑报告文本 → skeptic_section（注入LLM）
├── 4. S池优先标的合并入 scored_stocks
├── 5. 涨停/跌停过滤
├── 6. 7日内重复推荐保护
├── 7. 二审制Gate: 阻塞计数+连续降级
├── 8. Gate过滤: 从scored_stocks移除blocked
├── 9. LLM调用 → 生成决策报告
├── 10. 硬规则: check_hard_rules() (ST/亏损/退市/T+1)
├── 11. pool_updater.update_s_pool() → S池写入
│   ├── 价格位置检查 (52周高位)
│   ├── 跨池重叠清理 (重点池移除)
│   └── QualityGate.check() (市场状态+历史表现+过热)
└── 12. TrackRecorder 记录决策 + 闭环追踪
```

**校验**: ✅ 多层Gate，从审查→质疑→决策→写入层层过滤。

**潜在缺陷**:
- `check_hard_rules()` 在 orchestrator 中仅做关键词检查（ST/亏损/退市），与 ReviewAgent 的硬淘汰逻辑不共用
- QualityGate 在 PoolUpdater 中被调用，但与 `gate_controller.py` 中的 Gate 不共享状态

### 5.2 数据链断连校验：评分 → PoolUpdater → S池 → 下次决策读取

```
决策报告生成的评分
    │
    ▼
PoolUpdater._extract_score()  ← 正则提取（可能失败）
    │
    ▼
S级操作池.json → {"综合评分": score}
    │
    ▼
下次决策时 DecisionAgent 读取 S级操作池
    │
    ├── pool_updater.clean_expired_s_pool() → 检查纳入日期
    └── self._active_s_stocks = [s for s in s_pool_stocks if s_date == today]
```

**校验**:
1. 评分通过 `_extract_score()` 正则提取 × （有失败风险）
2. 池文件 JSON 写入 ✅ （结构化）
3. 下次决策读取 `s.get("综合分", 0)` ✅
4. 仅读取今日标的 (`s_date == today`) ✅

**结论**: 数据链完整但评分提取环节脆弱（依赖正则）。

### 5.3 T+1 追踪与过期清理

| 机制 | 频率 | 行为 |
|------|------|------|
| **S池 T+1清理** | 每次决策前 | `clean_expired_s_pool(max_age_days=1)` → 停留>1天的标的被移除 |
| **S级过期回流** | T+1后 | 标的→重点观察池（85%等比衰减）或→边缘池（T+1表现差） |
| **候选池14天淘汰** | 周末/定时 | `clean_expired_candidates(max_age_days=14)` |
| **边缘池30天淘汰** | 周末/每日 | `clean_expired_edge_pool(max_age_days=30 or 45)` |
| **T+1数据采集** | 每个工作日 | 从闭环追踪JSON读取昨日表现 → `record_t1_performance()` |
| **评分衰减** | 每次读取 | 入池>7天开始衰减0.5分/天，上限15分，下限40分 |

**评估**: ✅ T+1 过期清理完整闭环。
**潜在缺陷**: 
- 边缘池 `clean_expired_edge_pool()` 默认 `max_age_days=45`，与 `thresholds.EDGE_POOL_STALE_DAYS=30` 不一致
- 候选池清理仅在周末执行，工作日新标的入池后超14天不会被及时清理

---

## 6. 关键发现列表

### P0 — 必须修复

| # | 发现 | 影响 | 文件 |
|---|------|------|------|
| F01 | **边缘池清理天数不一致**: `pool_manager.clean_expired_edge_pool()` 默认45天，但 `thresholds.EDGE_POOL_STALE_DAYS=30`，导致陈旧标的滞留多15天 | 边缘池低效膨胀 | `pool_manager.py L461` vs `thresholds.py L177` |
| F02 | **候选池过期清理仅在周末执行**: 工作日入池的标的若超14天，需等到周末才被清除 | 候选池可能滞留过时标的 | `main.py L692-695` |
| F03 | **PoolUpdater 评分正则提取脆弱**: `_extract_score()` 依赖LLM输出格式，格式变化后 fallback 到0分，S池标的评分丢失 | S池评分可能为0 | `pool_updater.py L278-297` |

### P1 — 建议修复

| # | 发现 | 影响 | 文件 |
|---|------|------|------|
| F04 | **弱市简化审查绕过首次high豁免**: 弱市纯规则审查不生成 `block_reason` 字段，GateController 首次high豁免逻辑无法工作 | 弱市下首次阻塞的标的被直接阻断（无豁免机会） | `main.py L956-1003` |
| F05 | **熔断器状态不持久化**: 进程重启后熔断器重置，历史失败记录丢失 | 跨天熔断保护缺失 | `error_handling.py` |
| F06 | **S池超出容量无告警**: 超过3只主推时静默截断，无飞书通知 | 第4+只标的丢失无反馈 | `pool_manager.py L135-145` |
| F07 | **创业板独跌不触发熔断**: 熔断检测仅看上证<-3%，不覆盖创业板单独暴跌 | 创业板暴跌场景未处理 | `feedback_loop.py L126-144` |
| F08 | **跨天标日期解析异常静默跳过**: `datetime.strptime` 异常时 `pass`，标的被静默保留 | 日期格式异常导致S池过期标的未被清理 | `pool_manager.py L440-444` |

### P2 — 架构预判

| # | 发现 | 影响 |
|---|------|------|
| F09 | **决策-执行断链**: 决策方案（买入价/止损/止盈）无实际券商接口执行，停留在建议层 | T+1追踪仅记录涨跌幅，不构成完整交易闭环 |
| F10 | **双盲设计弱化 S级驱动筛选**: ReviewAgent 看不到快筛阶段的驱动上下文 | S级驱动标的可能在审查中丢失加分 |
| F11 | **结构化数据桥接缺失**: 各 Agent 间主要靠 Markdown 文件+正则提取传递数据，无正式 schema 桥接 | 格式依赖 LLM 一致性 |

---

## 7. 五池当前状态（审计快照）

| 池 | 目前数量 | 容量上限 | 使用率 |
|----|---------|---------|-------|
| 快筛候选池 | 18只 | 20只 | 90% |
| 重点观察池 | 9只 | 20只 | 45% |
| S级操作池 | 0只 | 3只 | 0% |
| 边缘池 | 30只 | 30只 | **100%** 🔴 |
| 持仓池 | 0只 | 无上限 | 0% |
| 重点观察池_历史池 | 33只 | 200只 | 17% |

> **注意**: 边缘池已满（30/30），新的降级标的将导致旧标的被静默截断。建议立即审查边缘池内容并清理达标标的。

---

## 8. 全链路依赖图

```
main.py
 ├── orchestrator.py (规则驱动调度)
 ├── news_agent.py (1次LLM) → 宏观前置分析.md
 ├── market_agent.py (行情获取) → shared_memory.json
 ├── screen_agent.py (1次LLM) → 快筛报告.md + 快筛候选池.json
 │    └── schemas.py (ScreenOutput/ScreenResult)
 ├── review_agent.py (1次LLM) → 审查报告.md + 五池更新
 │    ├── review_scorer.py (OverheatDetector, 纯规则)
 │    ├── quality_gate.py (历史表现+市场状态, 纯规则)
 │    ├── pool_manager.py (池读写)
 │    └── schemas.py (ReviewOutput/ReviewResult)
 ├── skeptic_agent.py (1次LLM, 弱市0次) → 质疑审查报告.md + 裁决.json
 │    ├── gate_controller.py (二审制Gate, 纯规则)
 │    └── market_agent.py (实时行情) 
 ├── decision_agent.py (1次LLM) → 决策报告.md + S级操作池
 │    ├── pool_updater.py (S池写入+价格检查)
 │    │    └── quality_gate.py (硬性质检门)
 │    ├── gate_controller.py (二审制Gate阻塞处理)
 │    ├── track_recorder.py (历史记录)
 │    │    └── closed_loop_tracker.py (T+1追踪)
 │    └── decision_utils.py (评分提取)
 ├── pool_manager.py (五池CRUD)
 ├── feedback_loop.py (市场熔断+持仓分析+胜率)
 │    └── closed_loop_tracker.py
 └── tianshu_memory.py (MemPalace记忆系统)
```

---

*审计报告生成: 子任务C 全链路审计完成*