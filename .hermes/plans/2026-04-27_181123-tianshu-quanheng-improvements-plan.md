# 天枢权衡系统改进计划

## 目标
改进天枢权衡多Agent股票分析系统的架构、可维护性、错误处理和扩展性。

## 当前状态/假设
- 系统目前正常运行，能够执行新闻分析、快筛、审查、决策等阶段。
- 已经识别出若干架构和代码质量问题（见之前的审查报告）。
- 团队希望在不破坏现有功能的前提下进行改进。

## 提出的方法
采用渐进式改进策略，专注于高影响力、低风险的更改。计划分为几个阶段：
1. 代码质量和可维护性改进（基类、标准化、错误处理）
2. 架构解耦（消息传递、池管理器）
3. 配置和可观察性改进
4. 高级特性（插件架构、改进调度）

## 分步计划

### 第一阶段：代码质量基础（估计：2-3天）

**已完成 ✅**：
- [x] 创建Agent基类（`agents/base_agent.py`）
  - 提取共享的LLM调用逻辑，包含重试机制
  - 提供统一的错误处理和日志记录
  - 标准化文件操作方法（safe_read_json, safe_write_json等）
  - 提供add_market_prefix和validate_and_prefix_codes工具函数
  - 添加统计信息跟踪（llm_calls, llm_errors等）
- [x] 创建单元测试（`tests/test_base_agent.py`）
- [x] 创建Pool管理器类（`agents/pool_manager.py`）
  - 集中所有池操作（读取、写入、验证）
  - 提供池数据的标准化接口
  - 支持股票移动（move_stock）
  - 兼容旧字段名（ standardize_stock）
- [x] 创建配置文件（`config.yaml`）
  - 将硬编码值移至配置文件
  - 包括API配置、池配置、筛选规则、权重等

### 第二阶段：架构解耦（估计：3-5天）

**进行中 🔄**：
- [x] 创建PoolManager类已完成 ✅
- [x] 添加结构化日志（`agents/logger.py`）✅
  - 支持JSON和文本格式
  - 上下文管理器自动记录执行时间
  - 专门方法记录Agent/LLM/池操作状态
- [x] 重构feedback_loop.py使用新模块 ✅
  - 继承BaseAgent
  - 使用PoolManager
  - 使用StructuredLogger
  - 保留原接口兼容性
- [x] 重构news_agent.py使用BaseAgent ✅
- [x] 重构screen_agent.py使用BaseAgent ✅
- [x] 重构review_agent.py使用BaseAgent ✅
- [x] 重构decision_agent.py使用BaseAgent ✅
- [x] 重构orchestrator.py使用PoolManager ✅
- [x] 添加单元测试 ✅
  - `tests/test_base_agent.py` - BaseAgent测试
  - `tests/test_pool_manager.py` - 池管理测试
  - `tests/test_agents_refactor.py` - Agent重构验证

**第二阶段全部完成** ✅

### 第三阶段：配置和可观察性（估计：2-3天）✅
1. ✅ 创建配置管理系统
   - ✅ agents/config_loader.py - 支持环境变量覆盖的YAML配置加载器
   - ✅ config.yaml 已包含所有硬编码配置值
2. ✅ 添加结构化日志
   - ✅ agents/logger.py - JSON格式结构化日志
3. ✅ 实现基本指标收集
   - ✅ agents/metrics.py - 跟踪LLM调用、执行时间、成功/失败率
   - ✅ main.py 新增 `metrics` 命令查看指标
4. ✅ 添加健康检查端点
   - ✅ agents/health.py - 系统健康检查
   - ✅ main.py 新增 `health` 命令检查系统状态

**第三阶段新增文件:**
- `agents/config_loader.py` - 配置加载器
- `agents/metrics.py` - 指标收集器
- `agents/health.py` - 健康检查器
- `tests/test_phase3_config_observability.py` - 第三阶段测试

**第三阶段验证:**
- ✅ 健康检查: 6项检查全部通过
- ✅ 配置加载: API URL、模型、温度等参数正确读取
- ✅ 指标收集: 运行指标正确记录
- ✅ BaseAgent集成: 从配置读取LLM参数

### 第四阶段：高级特性（估计：5-7天）✅
1. ✅ 插件架构 for Agents
   - ✅ agents/plugin_manager.py - Agent 热插拔和自动发现
   - ✅ 正则表达式扫描源代码发现 Agent 类
   - ✅ Agent 装饰器 @agent_plugin
2. ✅ 改进调度系统
   - ✅ agents/scheduler.py - Cron 表达式和灵活时间窗口
   - ✅ CronParser 解析器
   - ✅ 支持 interval / time_window / cron 三种调度模式
3. ✅ 容器化支持
   - ✅ Dockerfile - 多阶段构建
   - ✅ docker-compose.yml - 服务编排
   - ✅ .dockerignore - 优化镜像大小
   - ✅ requirements.txt - 依赖管理
4. ✅ 高级错误处理和故障转移
   - ✅ agents/error_handling.py - 熔断器模式 + 重试策略
   - ✅ CircuitBreaker 类 - 三态熔断（关闭/打开/半开）
   - ✅ RetryStrategy 类 - 指数退避 + 抖动

**第四阶段新增命令:**
- `python main.py agents` - 查看 Agent 列表
- `python main.py circuit` - 查看熔断器状态
- `python main.py schedule` - 查看调度任务

**第四阶段验证:**
- ✅ Agent 发现: 成功发现 4 个 Agent (NewsAgent, ScreenAgent, ReviewAgent, DecisionAgent)
- ✅ 调度器: 支持 cron 表达式 (如 "30 7 * * *")
- ✅ 熔断器: 三态转换正常
- ✅ Docker: Dockerfile 和 docker-compose.yml 已创建

## 下一步

**所有阶段已完成！** 系统已具备：
1. 模块化的 Agent 架构
2. 集中的配置和日志管理
3. 完整的可观察性（健康检查、指标收集）
4. 插件化的 Agent 发现机制
5. 灵活的调度系统
6. 高级错误处理（熔断器、重试）
7. 容器化部署支持

可选的后续工作：
- 集成 APscheduler 实现真正的定时任务
- 添加 Prometheus/Grafana 监控
- 完善集成测试和 E2E 测试

### 第四阶段：高级特性（可选，估计：5-7天）
1. 插件架构 for Agents
   - 使得可以在不修改核心代码的情况下添加新Agent
   - 发现机制（例如，agents/ 目录下的特定命名约定）
2. 改进调度系统
   - 替换硬编码时间窗口为可配置的调度器（如APScheduler）
   - 支持 cron-like 表达式或间隔调度
3. 容器化支持
   - 创建Dockerfile和docker-compose.yml
   - 文档化部署过程
4. 高级错误处理和故障转移
   - 熔断器模式用于外部服务
   - 副本Agent或备用数据源

## 可能变更的文件
- 创建：.hermes/plans/ (此计划)
- 新增：
  - agents/base_agent.py
  - agents/pool_manager.py
  - agents/message_bus.py (可选)
  - config/ (目录及配置文件)
  - tests/ (目录及测试文件)
- 修改：
  - main.py (移除全局LLM_CALL_COUNT，使用新的配置/日志)
  - agents/orchestrator.py (使用池管理器，可能的调度改进)
  - agents/news_agent.py
  - agents/screen_agent.py
  - agents/review_agent.py
  - agents/decision_agent.py
  - agents/market_agent.py
  - agents/feedback_loop.py
  - 五池管理/ 下的所有JSON文件（如果标准化字段名）
  - data/ 下的相关文件（如果受影响）

## 测试和验证
1. 单元测试
   - 为所有新类（BaseAgent，PoolManager等）编写测试
   - 为现有Agent的关键方法编写测试（模拟LLM API）
2. 集成测试
   - 验证完整工作流程（新闻→快筛→审查→决策）仍然正常工作
   - 测试池同步和数据流
3. 手动验证
   - 运行系统并检查飞书卡片输出
   - 验证持仓管理工具仍然正常工作
   - 检查日志和指标输出
4. 性能基准
   - 比较改进前后的执行时间（应相似或略有改善）
   - 验证没有引入重大延迟

## 风险、权衡和未解决的问题
### 风险
1. **向后兼容性**：更改池数据格式可能破坏依赖旧格式的外部工具。
   - 缓解：提供迁移脚本，并在过渡期间同时支持两种格式。
2. **引入错误**：重构可能会在原本有效的代码中引入bug。
   - 缓解：广泛的单元测试和手动验证。
3. **过度工程**：为简单系统添加过多抽象。
   - 缓解：从最小的改变开始，仅在需要时才添加复杂性。
### 权衡
1. **开发时间 vs. 长期可维护性**：投资时间进行重构将延迟新功能，但将减少未来的维护负担。
2. **性能**：添加抽象层可能会引入微小的开销。
   - 缓解：保持抽象层薄，并进行性能基准测试。
### 未解决的问题
1. 如何处理既有数据（池JSON文件）在标准化期间？
2. 应该使用哪种消息传递机制（简单队列 vs. 成熟的如Redis）？
3. 系统应该如何处理部分失败（例如，一个Agent失败而其他Agent成功）？

## 下一步
1. 审查此计划并根据反馈进行调整。
2. 开始第一阶段：创建Agent基类并重构一个Agent（例如，news_agent.py）作为试点。
3. 根据试点结果调整方法，然后继续处理其他Agent。
