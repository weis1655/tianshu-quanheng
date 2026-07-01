#!/usr/bin/env python3
"""
Decision Agent - 决策 Agent（重构版）
基于审查通过的股票池，形成完整执行方案
1次LLM调用

设计原则：
- 只看 Review Agent 评分≥75 的股票
- 每个决策必须包含：仓位/止损/止盈/触发条件/失效条件
- 永远输出"不做的情况"

继承BaseAgent获得：
- 统一的LLM调用（指数退避重试）
- 安全文件读写
- 统计跟踪
"""

import json
import re
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from safe_file_utils import safe_read_file

logger = logging.getLogger(__name__)

from base_agent import BaseAgent, build_agent_system_prompt
from logger import StructuredLogger
from pool_manager import PoolManager
from schemas import DecisionOutput, DecisionResult, ExecutionPlan
from schemas import DECISION_SCHEMA
from pool_updater import PoolUpdater
from track_recorder import TrackRecorder
from gate_controller import GateController
from decision_utils import extract_scores, build_empty_decision

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))


ROLE_PROMPT = """你是一个短线交易决策专家，专门为盟主制定完整执行方案。

盟主背景：
- 资金：10万基础 + 可增投10万
- 风格：短线、快进快出、盈利优先
- 单票最大仓位：30%
- 单笔最大亏损：5%
- 日内最大亏损：7%
- 止损线：3%
- 止盈目标：8%
- 制度：T+1

你的任务：
1. 接收审查通过的股票列表
2. 为每只股票制定完整执行方案

输出格式（每只股票必须包含）：
```
### 【主推/备选】股票名称（代码）
━━━━━━━━━━━━━━━━
📍 池子位置：xxx
🎯 核心驱动：驱动级别 + 驱动描述
💡 逻辑支撑：1-2句话描述逻辑
📊 技术形态：当前形态描述
📈 指数环境：大盘配合情况

💰 执行方案
• 单笔仓位：X%（根据综合评分和风险决定）
• 买入方式：追涨/回调买入
• 触发条件：具体价格条件
• 止损线：具体价格（亏损约X%）
• 止盈方案：①第一目标价（X%）→卖1/2 ②第二目标价（X%）→清仓
• 失效条件：跌破X元或X日内不启动

⚠️ 不做的情况（至少3条）
• 情况1
• 情况2
• 情况3

🛡️ 风险提示
• 风险1
• T+1提醒：今日买入不可卖
```

注意：
- 如果没有审查通过的股票（评分<70），输出"今日暂无通过审查的股票，建议空仓等待"
- 单票仓位最高不超过30%，一般推荐10-20%
- 止损和止盈必须明确写价格，不能只写百分比
- 永远输出"不做的情况"，这是风控底线"""


USER_PROMPT_TEMPLATE = """请根据以下审查报告，为通过审查的股票制定完整执行方案：

{review_report}

今日大盘环境：{market_env}

五池状态：{candidate_pool}

**原始新闻**见：{history_dir}/{today}_宏观前置分析.md

请只对评分≥75分的股票制定执行方案。
     如果无≥75分的股票，请输出"今日暂无通过审查的股票"，并列出60-74分（黄色预警）的备选观察标的及其关注要点。
     低于60分的不输出。"""


class DecisionAgent(BaseAgent):
    """决策 Agent（继承BaseAgent）"""

    def __init__(self, agent_name: str = "DecisionAgent"):
        super().__init__(agent_name)
        self.history_dir = self.root / "data" / "历史记录"
        self.pool_dir = self.root / "五池管理"
        self.logger = StructuredLogger("DecisionAgent")
        self.pool_manager = PoolManager()
        self.pool_updater = PoolUpdater(self.root, self.pool_manager)
        self.track_recorder = TrackRecorder(self.root, self.history_dir, self.pool_manager)
        # 强制清除 market_agent 模块缓存，避免残留旧代码
        import sys
        for mod in list(sys.modules.keys()):
            if 'market_agent' in mod:
                del sys.modules[mod]
        try:
            from market_agent import fetch_quotes
            self._fetch_quotes = fetch_quotes
        except Exception:
            self._fetch_quotes = None
        # 涨停/跌停排除集（_build_realtime_section 填充，_run_impl 消费）
        self._limit_up_excluded_codes = set()

    def run(self, review_report: Optional[str] = None, pools: Optional[dict] = None, wake_ctx: str = "") -> dict:
        """执行决策"""
        with self.logger.agent_action("run"):
            return self._run_impl(review_report, pools, wake_ctx)

    def _inject_evo_history(self, scored_stocks: list) -> str:
        """注入候选股的历史决策摘要（记忆闭环一部分）。
        通过 TrackRecorder 委托。
        """
        return self.track_recorder.inject_evo_history(scored_stocks)

    def _load_skeptic_context(self, today: str):
        """加载 Skeptic 质疑上下文，并返回注入 LLM 的文本与阻塞代码。"""
        skeptic_file = self.history_dir / f"{today}_质疑审查报告.md"
        verdict_file = self.history_dir / f"{today}_质疑审查裁决.json"
        skeptic_section = ""
        blocked_codes = set()
        skeptic_missing = False
        skeptic_empty = False
        skeptic_content = ""

        # 二审制Gate：先读取结构化裁决 JSON
        if verdict_file.exists():
            verdict_data = self.safe_read_json(verdict_file, {})
            blocked_list = verdict_data.get("blocked", [])
            blocked_codes = {s.get("code", "") for s in blocked_list}
            if blocked_codes:
                self.logger.info("skeptic_gate_blocked",
                               count=len(blocked_codes),
                               codes=list(blocked_codes))
                print(f"[二审制Gate] 🔴 质疑裁决阻塞 {len(blocked_codes)} 只标的: {blocked_codes}")

        if skeptic_file.exists():
            skeptic_content = self.safe_read_text(skeptic_file)
            if len(skeptic_content.strip()) < 50:
                skeptic_empty = True
                skeptic_section = ""
            else:
                skeptic_section = (
                    "\n\n## 📋 质疑审查报告（供参考）\n"
                    "以下为 SkepticAgent 对重点观察池标的的质疑分析：\n\n"
                    + skeptic_content + "\n"
                )
        else:
            skeptic_missing = True
            print("[二审制Gate] ⏭️ SkepticAgent跳过（今日无review升级标的），视为质疑通过")
            skeptic_section = ""

        return skeptic_section, blocked_codes, skeptic_missing, skeptic_empty, skeptic_content

    def _run_impl(self, review_report: Optional[str], pools: Optional[dict], wake_ctx: str = "") -> dict:
        today = datetime.now().strftime("%Y-%m-%d")

        # ── P0-2：S级操作池 T+1 过期清理 ────────────────────────
        expired_result = self.pool_manager.clean_expired_s_pool(max_age_days=1)
        # ── P0-2: S级过期标的→重点观察池（保留回流机会）─────
        if expired_result.get("removed"):
            for r in expired_result["removed"]:
                # P0: S级过期回流—等比衰减而非硬编码70分
                original_score = r.get("综合分", 80)
                if original_score is not None:
                    try:
                        decay_score = max(65, int(float(original_score) * 0.85))
                    except (TypeError, ValueError):
                        decay_score = 70
                else:
                    decay_score = 70
                key_stock = {
                    "代码": r.get("代码", ""),
                    "名称": r.get("名称", ""),
                    "综合分": decay_score,  # 等比衰减，不硬编码70
                    "纳入日期": datetime.now().strftime("%Y-%m-%d"),
                    "驱动来源": r.get("driver_source", "S级过期降级"),
                    "核心逻辑": f"源自S级操作池过期降级（原始{r.get('综合分', '?')}分→衰减{decay_score}分，停留{r.get('停留天数', 1)}天）",
                }
                # Gate守卫检查：跨池重复
                dup = GateController.check_cross_pool_duplicate(r['代码'], exclude_pool="重点观察池", pool_manager=self.pool_manager)
                if dup:
                    r['_cross_pool'] = dup
                    print(f"[Gate] ⚠️ {r['名称']} 同时存在于 {dup}")

                # Gate守卫检查：容量校验 + 写入规则
                # P2-2026-06-04: S级过期回流是受控路径，显式允许跨池
                r['allow_cross_pool'] = True
                rule = GateController.enforce_writing_rules(r, "重点观察池", pool_manager=self.pool_manager)
                if not rule['allowed']:
                    print(f"[Gate] 🚫 {r['名称']} 被守卫拦截: {rule['reason']}")
                else:
                    self.pool_manager.add_stock("重点观察池", key_stock)
                    print(f"[S级回流] ⬆️ {r['名称']}({r['代码']}) → 重点观察池（S级过期回流，{rule.get('reason','')}）")

        # 读取审查报告
        if review_report is None:
            review_file = self.history_dir / f"{today}_审查报告.md"
            if review_file.exists():
                review_report = self.safe_read_text(review_file)
            else:
                return {"success": False, "error": "没有找到今日审查报告"}

        # ── 记忆闭环：注入 SkepticAgent 质疑结果（二审制 Gate）───
        skeptic_section, blocked_codes, skeptic_missing, skeptic_empty, skeptic_content = \
            self._load_skeptic_context(today)
        # ────────────────────────────────────────────────────────

        if len(review_report) < 50:
            return {"success": False, "error": "审查报告内容不足"}

        # ── P1-2：决策前置条件检查 ─────────────────────────────
        # 质疑报告缺失时已注入缺失声明（见上），不阻塞流程
        # 空质疑报告（skeptic_empty=True）视为"无质疑"，不阻断流程
        # ────────────────────────────────────────────────────────
        # 检查2：实时行情数据是否齐备
        pools = self._load_pools() if pools is None else pools
        # P3修复：从审查报告中提取所有评分股票代码，强制注入行情查询
        extra_codes = []
        if review_report:
            # 匹配 ## 600547 山东黄金 或 ## 600547(山东黄金) 等多种格式
            extra_codes = re.findall(r'##\s*\[?(\d{6})\]?\s*[（(]?', review_report)

        # ── P0-3：S级操作池优先读取（优先于审查报告）────────────
        s_pool_data = pools.get("S级操作池", {})
        s_pool_stocks = s_pool_data.get("stocks", []) if isinstance(s_pool_data, dict) else []
        today = datetime.now().strftime("%Y-%m-%d")
        self._active_s_stocks = []
        s_pool_section = ""
        if s_pool_stocks:
            for s in s_pool_stocks:
                s_code = str(s.get("代码", s.get("股票代码", "")))
                s_date = str(s.get("纳入日期", ""))
                if s_date == today and s_code:
                    self._active_s_stocks.append(s)
                    if s_code not in extra_codes:
                        extra_codes.append(s_code)
            if self._active_s_stocks:
                s_lines = []
                for s in self._active_s_stocks:
                    s_name = s.get("名称", s.get("股票名称", "?"))
                    s_code = str(s.get("代码", s.get("股票代码", "")))
                    s_logic = s.get("核心逻辑", s.get("driver", ""))
                    s_price = s.get("入场价", s.get("入场价格", "待确认"))
                    s_line = f"- {s_name}({s_code}) S级主推 | 入场参考价:{s_price}"
                    if s_logic:
                        s_line += f" | 逻辑:{s_logic}"
                    s_lines.append(s_line)
                s_pool_section = (
                    "\n\n## 🏆 S级操作池（今日有效标的 — 跳过审查，优先执行）\n"
                    + "\n".join(s_lines)
                    + "\n\nS级操作池今日已审查通过，直接进入决策层，无需再次审查。"
                )
                print(f"[S级优先] ⭐ 今日S级操作池有 {len(self._active_s_stocks)} 只有效标的, 已注入extra_codes")
                self.logger.info("s_pool_priority",
                               count=len(self._active_s_stocks),
                               codes=[s.get("代码","") for s in self._active_s_stocks])
        # ──────────────────────────────────────────────────────────────
        realtime_section = self._build_realtime_section(pools, extra_codes=extra_codes)
        if not realtime_section:
            return {
                "success": False,
                "error": "实时行情数据缺失 — 无法制定精确止损/止盈方案",
                "missing_precondition": "实时行情",
            }
        # ────────────────────────────────────────────────────────

        # 读取大盘环境（二审制Gate需要）
        market_env = self._get_market_env()
        # ── Level-2: 市场状态预判（决定 S 池推荐数量）───
        market_state = self._get_market_state()
        # 动态覆盖 S 池容量：下跌市减少推荐
        self._market_state = market_state

        # 提前提取评分（二审制Gate需要）
        scored_stocks = self._extract_scores(review_report)

        # ── P0-3：将S级操作池有效标的合并入评分列表（优先于审查报告）─
        if self._active_s_stocks:
            existing_codes = {str(s.get("code", s.get("代码", ""))) for s in scored_stocks}
            for s in self._active_s_stocks:
                s_code = str(s.get("代码", s.get("股票代码", "")))
                s_name = s.get("名称", s.get("股票名称", "?"))
                if s_code not in existing_codes:
                    scored_stocks.append({
                        "code": s_code,
                        "name": s_name,
                        "score": 80,  # S级准入分
                        "passed": True,
                        "ml_score": None,
                    })
                    existing_codes.add(s_code)
                    self.logger.info("s_pool_merged_into_review",
                                   code=s_code, name=s_name, score=80)
            if self._active_s_stocks:
                print(f"[S级优先] 🔀 {len(self._active_s_stocks)} 只S级标的已合并入 scored_stocks（评分80分）")
        # ──────────────────────────────────────────────────────────────

        # ── 涨停/跌停过滤：从评分列表和池中移除封板标的 ──────────
        if self._limit_up_excluded_codes:
            excluded = self._limit_up_excluded_codes
            before_count = len(scored_stocks)
            scored_stocks = [s for s in scored_stocks
                             if str(s.get("code", s.get("代码", ""))) not in excluded]
            # 同步从 pools 中移除涨停/跌停股
            for pool_name, pool_data in pools.items():
                if pool_data.get("stocks"):
                    pool_data["stocks"] = [
                        s for s in pool_data["stocks"]
                        if str(s.get("代码", s.get("股票代码", ""))) not in excluded
                    ]
            removed = before_count - len(scored_stocks)
            if removed:
                print(f"[涨停排除] ⛔ {removed} 只候选股已涨停/跌停，已从决策池移除: {excluded}")
                self.logger.info("limit_up_filtered", removed=removed, codes=list(excluded))
            if not scored_stocks:
                if before_count > 0:
                    # scored_stocks 本来有标的，但全被涨停排除过滤掉了
                    print("[涨停排除] ✅ 所有候选股均涨停/跌停，执行空仓决策")
                    return self._build_empty_decision(today, pools, market_env,
                                                       "涨停/跌停排除：所有候选标的已封板",
                                                       yellow_alerts=[])
                # scored_stocks 本来就空（如无审查结果），不归咎于涨停——让后续流程继续评估池内标的
        # ──────────────────────────────────────────────────────────

        # ═══ P0-修复（2026-06-10）：重复推荐保护 — 最近7天推荐过的标的过滤 ═══
        dup_codes = set()
        dup_names = set()
        dt_now = datetime.now()
        from datetime import timedelta
        try:
            verify_file = self.root / "data" / "full_sampling_verify.json"
            if verify_file.exists():
                import json
                vdata = json.loads(verify_file.read_text(encoding="utf-8"))
                recent_dates = set()
                for i in range(7):
                    d = (dt_now - timedelta(days=i)).strftime("%Y-%m-%d")
                    recent_dates.add(d)
                for entry in vdata.get("stocks", []):
                    if entry.get("entry_date", "") in recent_dates:
                        code = str(entry.get("code", "")).strip()
                        if code:
                            dup_codes.add(code)
                            if entry.get("name"):
                                dup_names.add(entry["name"])
            # 降级路径2：从历史决策报告目录扫描最近7天的推荐
            if self.history_dir and self.history_dir.exists():
                for i in range(7):
                    d = (dt_now - timedelta(days=i)).strftime("%Y-%m-%d")
                    fp = self.history_dir / f"{d}_决策报告.md"
                    if fp.exists():
                        content = fp.read_text(encoding="utf-8", errors="replace")
                        for m in set(re.findall(r"[（(](\d{6})[）)]", content)):
                            dup_codes.add(m)
        except Exception:
            pass
        if dup_codes:
            before = len(scored_stocks)
            dup_in_picks = {s["code"] for s in scored_stocks if str(s.get("code","")) in dup_codes}
            scored_stocks = [s for s in scored_stocks if str(s.get("code", "")) not in dup_codes]
            for pool_name, pool_data in pools.items():
                if pool_data.get("stocks"):
                    pool_data["stocks"] = [
                        s for s in pool_data["stocks"]
                        if str(s.get("代码", s.get("股票代码", ""))) not in dup_codes
                    ]
            if dup_in_picks:
                print(f"[重复推荐保护] 🚫 {len(dup_in_picks)} 只标的7日内已推荐，已过滤: {dup_in_picks}")
            # 如果过滤后为空，走正常空仓逻辑（不是硬返回，让后续逻辑决定）
        # ═══════════════════════════════════════════════════════════════════════

        # ── 二审制Gate：阻塞标的计数 + 连续3次自动降级 ──
        if blocked_codes:
            # 读取重点观察池JSON（磁盘上的原始数据）
            key_pool_file = self.root / "五池管理" / "重点观察池.json"
            if key_pool_file.exists():
                key_pool_data = self.safe_read_json(key_pool_file, {})
                if key_pool_data.get("stocks"):
                    # ── P1-2: 读取裁决JSON用于首次high豁免 ──
                    verdict_file = self.history_dir / f"{today}_质疑审查裁决.json"
                    verdict_data = None
                    if verdict_file.exists():
                        verdict_data = self.safe_read_json(verdict_file, {})
                    key_pool_data, demotions, resets, modified = GateController.process_focus_pool_blocked_counts(
                        key_pool_data, blocked_codes, verdict_data
                    )
                    for dm in demotions:
                        edge = {
                            "代码": dm["代码"],
                            "名称": dm["名称"],
                            "综合分": 60,  # 连续质疑不过，保守给60
                            "纳入日期": datetime.now().strftime("%Y-%m-%d"),
                            "驱动来源": "连续质疑阻塞降级",
                            "核心逻辑": f"被Skeptic连续质疑阻塞{dm['count']}次",
                        }
                        self.pool_manager.add_stock("边缘池", edge)
                        print(f"[二审制Gate] ⬇️ {dm['名称']}({dm['代码']}) → 边缘池（连续{dm['count']}次阻塞）")
                    for s in key_pool_data.get("stocks", []):
                        s_code = str(s.get("代码", s.get("股票代码", "")))
                        if s_code in blocked_codes and s.get("blocked_count", 0) > 0:
                            print(f"[二审制Gate] 🔴 {s.get('名称','?')}({s_code}) 被阻塞第{s['blocked_count']}次")
                        elif s.get("blocked_count", 0) == 0:
                            print(f"[二审制Gate] ✅ {s.get('名称','?')}({s_code}) 质疑通过，重置阻塞计数")
                    if modified:
                        self.safe_write_json(key_pool_file, key_pool_data)

        # ── 二审制Gate：从候选列表中移除被质疑拦截的标的 ────
        # 保存原始评分副本，供后续空仓决策/fallback兜底使用
        all_scored_stocks = scored_stocks[:] if scored_stocks else []
        if blocked_codes:
            pools = GateController.filter_pools(pools, blocked_codes)
            scored_stocks = GateController.filter_scored_stocks(scored_stocks, blocked_codes)
            if all_scored_stocks and GateController.is_all_blocked(all_scored_stocks, blocked_codes):
                print("[二审制Gate] ✅ 所有候选标的均被质疑拦截，执行空仓决策")
                yellow_alerts = GateController.get_yellow_alerts(all_scored_stocks)
                return self._build_empty_decision(today, pools, market_env,
                                                   "二审制Gate：所有候选标的均未通过质疑审查",
                                                   yellow_alerts=yellow_alerts)

        # 读取候选池和大盘环境（二审制Gate不阻塞时继续）
        candidate_pool = self._format_pools(pools)

        # P0-2: 从审查报告中提取结构化评分（行255已过滤Gate拦截标的，此处复用）
        scored_summary = self._format_scored_stocks(scored_stocks)

        # ── 记忆闭环：注入历史决策参考 ────────────────────────────
        evo_history = self._inject_evo_history(scored_stocks)
        # ──────────────────────────────────────────────────────────

        # ── P1-3：Skeptic 报告覆盖度检查 ─────────────────────────
        # 审查通过的标的如果未出现在质疑报告中，注入明确警告
        # 防止 LLM 自行困惑后输出"前置检查失败"
        coverage_warning = ""
        if not skeptic_empty and skeptic_content and len(skeptic_content.strip()) >= 50 and scored_stocks:
            skeptic_codes = self._extract_skeptic_covered_codes(skeptic_content)
            uncovered = [s for s in scored_stocks if s["code"] not in skeptic_codes]
            if uncovered:
                names = "、".join(f"{s['name']}({s['code']})" for s in uncovered)
                coverage_warning = (
                    f"\n\n## ⚠️ Skeptic覆盖度警告\n"
                    f"以下标的的审查评分≥75分，但**未出现在Skeptic质疑审查报告中**：\n"
                    f"{names}\n\n"
                    f"说明：SkepticAgent当期未对这些标的进行质疑审查，LLM在制定执行方案时"
                    f"需自行评估其质疑风险，或等待补全质疑后再做决策。\n"
                )
                self.logger.info("skeptic_coverage_gap",
                               uncovered=names, count=len(uncovered))
                print(f"[Skeptic覆盖度] ⚠️ {len(uncovered)} 只标的未质疑覆盖: {names}")
                # ── P1-3升级：否决式阻断 — 未覆盖标的从scored_stocks移除 ────
                # 避免"LLM自行评估风险"这种自欺欺人的处理
                # 既然Skeptic没审，那就不能进入决策候选
                self.logger.info("skeptic_block_uncovered",
                               removed=names, count=len(uncovered))
                print(f"[Skeptic阻断] 🚫 从决策候选移除 {len(uncovered)} 只未审查标的: {names}")
                for u in uncovered:
                    scored_stocks = [s for s in scored_stocks if s["code"] != u["code"]]
        # ────────────────────────────────────────────────────────────

        # LLM 决策
        self.logger.llm_call("make_decision", tokens=len(review_report))
        # 把评分结构注入prompt前端（加实时行情刷新 + 记忆闭环历史）
        header_parts = []
        if realtime_section:
            header_parts.append(realtime_section)
        if scored_summary:
            header_parts.append(scored_summary)
        if evo_history:
            header_parts.append(evo_history)
        # Level-2b：下跌市防御品种提示
        if market_state.get("state") in ["震荡偏弱", "偏空"]:
            header_parts.append(
                "\n\n## ⚠️ 市场偏弱提示\n"
                "当前市场环境偏弱，建议：\n"
                "1. 减少开仓，控制仓位\n"
                "2. 回避追高风险\n"
                "3. 如要推荐，优先考虑防御型品种（高股息/公用事业/消费龙头）\n"
                "4. S级操作池今日建议不超过1只\n"
            )
        if skeptic_section:
            header_parts.append(skeptic_section)
        if coverage_warning:
            header_parts.append(coverage_warning)
        # ── P0-3：S级操作池优先注入（LLM可见）───────────────
        if s_pool_section:
            header_parts.append(s_pool_section)
        # ────────────────────────────────────────────────────
        header_parts.append("请基于以上数据制定执行方案。\n")
        header_parts.append(USER_PROMPT_TEMPLATE.format(
            review_report=review_report[:6000],
            market_env=market_env,
            candidate_pool=candidate_pool,
            history_dir=str(self.history_dir),
            today=today,
        ))
        user_prompt = "\n\n".join(header_parts)
        result = self.call_llm(
            user_prompt,
            system=build_agent_system_prompt(ROLE_PROMPT, "DecisionAgent", extra_context=wake_ctx),
            max_tokens=3000
        )

        # P0-2: 若LLM返回空仓但有评分≥75的股票，先二次尝试（优先LLM方案）
        if any(k in result for k in ["暂无", "空仓", "等待", "观望", "不建议"]) and scored_stocks:
            # P0-2026-06-04: 只选择审查通过（passed=True）且评分≥75的标的
            # P0-2026-06-05: 防御性排除被SkepticGate阻塞的标的（虽然L338已过滤，兜底保护）
            actionable = [
                s for s in scored_stocks
                if s.get("score", 0) >= 75 and s.get("passed", False)
                and str(s.get("code", s.get("代码", ""))) not in blocked_codes
            ]
            if actionable:
                # ═══ P0-修复（2026-06-10）：准确率0%根因 — 市场状态检查 ═══
                market_state_cur = self._market_state if hasattr(self, '_market_state') else {"state": "震荡", "s_pool_cap": 2}
                
                # 偏空市场：尊重LLM的空仓判断，跳过兜底买入
                if market_state_cur.get("state") in ["偏空"]:
                    print(f"[兜底引擎] 🚫 市场状态=偏空，尊重LLM空仓判断，跳过兜底买入")
                    # 保留LLM原始空仓结果
                    pass
                
                # 震荡偏弱：改为观察建议，不生成买入执行方案
                elif market_state_cur.get("state") == "震荡偏弱":
                    best = actionable[0]
                    result = (
                        f"### 【观察】{best['name']}（{best['code']}）\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"📍 审查通过（综合评分{best['score']}分）\n"
                        f"💡 逻辑支撑：评分≥75分，驱动明确，但当前市场震荡偏弱\n"
                        f"⏳ 建议：观察等待，不急于入场\n"
                        f"• 观察条件：评分维持在≥75分，市场回暖确认\n"
                        f"• 失效条件：跌破关键支撑位或评分降至74分以下\n"
                        f"\n"
                        f"⚠️ 免责声明：此观察建议由兜底引擎自动生成，不构成买入建议。\n"
                    )
                    print(f"[兜底引擎] ⏳ 震荡偏弱市场，{best['name']}({best['code']}) {best['score']}分 → 仅建议观察")
                
                # 偏多/震荡偏强：执行原有兜底买入（保留LLM二次尝试+模板兜底）
                else:
                    # P0-实盘亏损修复：LLM-ML背离标的禁止兜底买入
                    _filtered_actionable = []
                    for a in actionable:
                        ml_score = a.get("ml_score", None)
                        llm_name = a.get("name", "?")
                        llm_code = a.get("code", "?")
                        if ml_score is not None and ml_score < 50:
                            print(f"[兜底引擎] 🚫 LLM-ML背离: {llm_name}({llm_code}) "
                                  f"LLM={a['score']} ML={ml_score} 跳过兜底买入")
                            continue
                        _filtered_actionable.append(a)
                    actionable = _filtered_actionable
                    if not actionable:
                        print(f"[兜底引擎] ⏭ 全部标的被LLM-ML背离过滤，跳过兜底买入")
                        return None
                    best = actionable[0]
                    # 构建SkepticGate上下文块（如有阻塞标的则提示LLM）
                    skeptic_context = ""
                    if blocked_codes:
                        blocked_names = []
                        for s_code in blocked_codes:
                            for s in all_scored_stocks:
                                if str(s.get("code", s.get("代码", ""))) == s_code:
                                    blocked_names.append(f"{s.get('name','?')}({s_code})")
                                    break
                        skeptic_context = (
                            "\n\n**⚠️ SkepticGate 二审制提示**：\n"
                            f"以下标的已被质疑者拦截（不可操作）：{'、'.join(blocked_names)}\n"
                            "请仅对未拦截的标的制定方案。\n"
                        )
                    override_prompt = f"""基于审查评分，强制制定执行方案：

{realtime_section}

**大盘环境**：
{market_env}

{skeptic_context}

{best['name']}（{best['code']}）综合评分：{best['score']}分

请基于以上实时行情和宏观环境，为该股票制定完整的买入执行方案（含仓位/买入价/止损触发价/第一目标价/第二目标价/触发条件/失效条件）。"""
                    result = self.call_llm(
                override_prompt,
                system=build_agent_system_prompt(ROLE_PROMPT, "DecisionAgent", extra_context=wake_ctx),
                max_tokens=1500
            )
                    self.logger.info("score_override", stock=best["code"], score=best["score"])
                    # P0-2修复：如果二次LLM仍然拒绝，用模板化方案兜底
                    if any(k in result for k in ["暂无", "空仓", "等待", "观望", "不操作"]):
                        now_price_text = "待查询"
                        blocker_note = ""
                        if blocked_codes:
                            blocker_note = "\n⚠️ 注意：该标的未被SkepticGate拦截，但决策时仍需谨慎。\n"
                        result = (
                            f"### 【主推】{best['name']}（{best['code']}）\n"
                            f"━━━━━━━━━━━━━━━━\n"
                            f"📍 池子位置：审查通过（综合评分{best['score']}分）\n"
                            f"💡 逻辑支撑：审查评分≥75分，基本面/技术面驱动明确\n"
                            f"💰 执行方案\n"
                            f"• 单笔仓位：10%（保守建仓，等待市场确认）\n"
                            f"• 买入方式：分批低吸（首次1/2仓位，确认后补1/2）\n"
                            f"• 止损线：现价下方3%（动态调整）\n"
                            f"• 第一止盈：+8%（卖1/2）\n"
                            f"• 第二止盈：+15%（清仓）\n"
                            f"• 失效条件：跌破止损线或3日内无有效启动\n"
                            f"{blocker_note}"
                            f"\n"
                            f"⚠️ 免责声明：此方案由兜底引擎自动生成，请结合个人判断使用。\n"
                        )
                        print(f"[模板兜底] ✅ 为 {best['name']}({best['code']}) {best['score']}分 生成模板化方案（偏多/震荡偏强市场）")

        # 格式化报告
        report = f"""# 【决策报告】{today}

━━━━━━━━━━━━━━━━

## 指数环境判断

### 今日行情
{market_env}

---

{result}

---
|决策执行时间：{datetime.now().strftime('%H:%M')}
"""

        # ── 先写S级操作池（含质检），再从池反推报告 ──────────────
        # 这样决策报告只反映真实落池的标的，池外标的不出现在报告中
        self._update_s_pool(result, scored_stocks=scored_stocks or [])
        
        # 读取S级操作池今日进入的标的
        pool_confirmed_codes = set()
        s_pool_today_codes = set()
        s_pool_path = self.pool_dir / 'S级操作池.json'
        if s_pool_path.exists():
            try:
                s_data = json.loads(s_pool_path.read_text(encoding='utf-8'))
                for h in s_data.get('历史记录', []):
                    if h.get('日期') == today:
                        for s in h.get('标的', []):
                            if isinstance(s, dict) and s.get('代码'):
                                pool_confirmed_codes.add(str(s['代码']))
                                s_pool_today_codes.add(str(s['代码']))
            except Exception:
                pass
        # 同时也读重点观察池中的标的（长效跟踪）
        kw_pool_path = self.pool_dir / '重点观察池.json'
        kw_data = {'stocks': []}
        if kw_pool_path.exists():
            try:
                kw_data = json.loads(kw_pool_path.read_text(encoding='utf-8'))
                for s in kw_data.get('stocks', []):
                    code = str(s.get('代码', ''))
                    if code:
                        pool_confirmed_codes.add(code)
            except Exception:
                pass
        
        # 从result中移除未落池的【主推】标的
        if pool_confirmed_codes:
            import re as _re2
            main_in_result = _re2.findall(r"【主推】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]", result)
            codes_to_remove = {mc[1] for mc in main_in_result if mc[1] not in pool_confirmed_codes}
            if codes_to_remove:
                new_lines, skip = [], False
                for line in result.split("\n"):
                    rm = _re2.match(r"###?\s*【主推】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]", line)
                    if rm and rm.group(2) in codes_to_remove:
                        skip = True
                        continue
                    if skip and (line.startswith("###") or line.startswith("---")):
                        skip = False
                        if line.startswith("###"):
                            new_lines.append(line)
                        continue
                    if not skip:
                        new_lines.append(line)
                result = "\n".join(new_lines)
        elif result and ('【主推】' in result or '【备选】' in result):
            # 池全空（全部被S池拒绝+重点池也空），清空result展示空仓
            if '空仓' not in result and '不操作' not in result:
                result_lines = result.split('\n')
                filtered = []
                skip = False
                for line in result_lines:
                    if line.startswith('###') or line.startswith('---'):
                        skip = False
                    if skip:
                        continue
                    if re.search(r'【主推】|【备选】', line):
                        skip = True
                        continue
                    if not line.startswith('###') and not line.startswith('---') and '━━' not in line:
                        filtered.append(line)
                result = '\n'.join([l for l in filtered if l.strip()]) or '📭 今日S级操作池：无标的通过质检'

        # 格式化报告
        s_pool_today = len(s_pool_today_codes)  # S池今日进入
        kw_count = len(kw_data.get('stocks', []))
        report = f"""# 【决策报告】{today}

━━━━━━━━━━━━━━━━

## 指数环境判断

### 今日行情
{market_env}

---

{result}

---

### 📋 池联动确认
- **S级操作池**: {s_pool_today}只今日主推标的
- **重点观察池**: {kw_count}只持续跟踪中
- **报告说明**: 本报告标的均来自S级操作池和重点观察池，池外推荐不展示
---
决策执行时间：{datetime.now().strftime('%H:%M')}
"""

        # 保存
        out_file = self.history_dir / f"{today}_决策报告.md"
        self.safe_write_text(out_file, report)

        # ── P0-2: S级操作池历史命中率评价 ──────────────────────
        report = self.track_recorder.record_s_pool_eval(report, out_file)

        # P1-3: 记录决策日志（含可验证假设，从五池直取核心逻辑兜底）
        self._record_to_evo(scored_stocks, result, review_report, pools=pools)

        # 生成汇总报告
        self._generate_summary(today)

        self.logger.info("decision_complete",
                        saved_to=str(out_file),
                        stats=self.get_stats())

        # ── 构建 DecisionResult（新增 schema 结构化输出）─────────────
        decision_result = self._parse_decision_result_v2(result, scored_stocks)
        
        # ── P2-3：闭环追踪记录 ──────────────────────────────
        from closed_loop_tracker import ClosedLoopTracker
        tracker = ClosedLoopTracker()
        try:
            for plan in decision_result.plans:
                tracker.record_decision(
                    code=plan.code,
                    name=plan.name,
                    priority=plan.priority,
                    plan={
                        "buy_method": plan.buy_method,
                        "position_pct": plan.position_pct,
                        "stop_loss": plan.stop_loss,
                        "target_1": plan.target_1_price,
                        "target_2": plan.target_2_price,
                    },
                )
        except Exception as e:
            self.logger.warning("closed_loop_decision_fail", error=str(e))

        # 保留旧 dict 返回格式供主流程兼容
        return {
            "success": True,
            "report": report,
            "raw_result": result,
            "saved_to": str(out_file),
            "decision_result": decision_result,  # 新增：结构化结果
            "plans": decision_result.plans,
            "main_tui": decision_result.main_tui,
        }

    def _parse_decision_result_v2(self, raw_text: str, scored_stocks: List[dict]) -> DecisionResult:
        """
        V2 解析：返回 DecisionResult 结构
        从 LLM 原始输出中提取执行方案（精简 regex，不过度兜底多种格式）
        """
        plans: List[ExecutionPlan] = []
        main_tui: List[ExecutionPlan] = []
        backup: List[ExecutionPlan] = []
        no_action_reason = ""

        # 检查空仓状态（P2修复：增强空仓判断，增加备选策略）
        if any(k in raw_text for k in ["暂无", "空仓", "等待", "观望", "建议空仓"]):
            no_action_reason = "无审查通过≥75分的股票，建议空仓等待"
            # 备选策略：检查是否有60-74分的"黄色预警"股票可观察
            yellow_watch = GateController.get_yellow_alerts(scored_stocks)
            if yellow_watch:
                # P0-2026-06-04: 备选观察阈值扩为60-74（与≥75决策阈值对齐）
                no_action_reason += f"\n🟡 备选观察（{len(yellow_watch)}只黄色预警标的，60-74分）："
                for i, s in enumerate(yellow_watch[:5], 1):
                    conf = s.get("confidence", "")
                    conf_str = f" [{conf}]" if conf else ""
                    no_action_reason += f"\n  {i}. {s.get('code','?')} {s.get('name','?')} ({s.get('score',0)}分{conf_str})"

        # 按 ### 标题分割（每个股票一个方案块）
        sections = re.split(r'(?=###\s+【)', raw_text)
        for section in sections:
            if not section.strip() or "【" not in section:
                continue

            # 提取代码和名称
            header_m = re.search(r'【(主推|备选|关注|推荐)\s*】?\s*([\u4e00-\u9fa5]{2,8})\s*[（(](\d{6})[）)]', section)
            if not header_m:
                continue
            priority = header_m.group(1)
            name = header_m.group(2)
            code = header_m.group(3)

            if code == "000000":
                continue

            # 防御性过滤：涨停/跌停排除（防止LLM误推封板股）
            if hasattr(self, '_limit_up_excluded_codes') and code in self._limit_up_excluded_codes:
                continue

            # 提取各字段（使用规范格式的正则）
            def _get_float(pat: str, default: float = 0.0) -> float:
                m = re.search(pat, section)
                return float(m.group(1)) if m else default

            def _get_str(pat: str, default: str = "") -> str:
                m = re.search(pat, section)
                return m.group(1).strip() if m else default

            position_pct = _get_float(r'单笔仓位[：:]\s*(\d+(?:\.\d+)?)%', 0.0)
            buy_method = _get_str(r'买入方式[：:]\s*([^\n]+)', "待确认")
            trigger_price = _get_float(r'触发条件[：:]\s*([\d.]+)\s*(?:元|块|价)', 0.0)
            stop_loss = _get_float(r'止损(?:线|触发)[：:]\s*([\d.]+)', 0.0)
            stop_loss_pct = _get_float(r'止损.*?(?:约)?(\d+)%', 5.0)

            target_1_price = _get_float(r'第一目标(?:价)?[：:]\s*([\d.]+)', 0.0)
            target_1_pct = _get_float(r'第一目标.*?(?:约)?(\d+(?:\.\d+)?)%', 10.0)
            target_1_action = _get_str(r'第一目标.*?→\s*([^ \n]+)', "卖1/2")
            target_2_price = _get_float(r'第二目标(?:价)?[：:]\s*([\d.]+)', 0.0)
            target_2_pct = _get_float(r'第二目标.*?(?:约)?(\d+(?:\.\d+)?)%', 20.0)
            target_2_action = _get_str(r'第二目标.*?→\s*([^ \n]+)', "清仓")

            invalid_condition = _get_str(r'失效条件[：:]\s*([^\n]{5,50})', "待确认")
            invalid_price = _get_float(r'失效条件.*?([\d.]+)\s*(?:元|块)', 0.0)

            # 核心驱动和逻辑
            driver = _get_str(r'核心驱动[：:]\s*([^\n]{3,60})', "")
            logic = _get_str(r'逻辑支撑[：:]\s*([^\n]{3,60})', "")
            tech_shape = _get_str(r'技术形态[：:]\s*([^\n]{3,60})', "")
            index_env = _get_str(r'指数环境[：:]\s*([^\n]{3,60})', "")
            pool_pos = _get_str(r'池子位置[：:]\s*([^\n]{3,30})', "")

            # 不做的情况
            no_go_lines = re.findall(r'(?:-|•|\*)\s*([^-\n]{5,60})', section[section.find("不做的情况"):section.find("风险提示") if "风险提示" in section else len(section)])
            no_go_rules = [l.strip() for l in no_go_lines[:5] if l.strip()]

            # 风险提示
            risk_lines = re.findall(r'(?:-|•|\*)\s*([^\n]{3,60})', section[section.find("风险提示"):])
            risk_notes = [l.strip() for l in risk_lines[:5] if l.strip() and "T+1" not in l]

            # 从评分数据中获取假设
            matched = next((s for s in scored_stocks if s.get("code") == code), {})
            hypothesis = matched.get("hypothesis", "") if isinstance(matched, dict) else ""
            expected_logic = matched.get("expected_logic", "") if isinstance(matched, dict) else ""

            plan = ExecutionPlan(
                code=code,
                name=name,
                priority=priority,
                pool_position=pool_pos,
                driver=driver,
                logic=logic,
                tech_shape=tech_shape,
                index_env=index_env,
                position_pct=position_pct,
                buy_method=buy_method,
                trigger_price=trigger_price,
                stop_loss=stop_loss,
                stop_loss_pct=stop_loss_pct,
                target_1_price=target_1_price,
                target_1_pct=target_1_pct,
                target_1_action=target_1_action,
                target_2_price=target_2_price,
                target_2_pct=target_2_pct,
                target_2_action=target_2_action,
                invalid_condition=invalid_condition,
                invalid_price=invalid_price,
                no_go_rules=no_go_rules,
                risk_notes=risk_notes,
                hypothesis=hypothesis,
                expected_logic=expected_logic,
            )
            plans.append(plan)
            if priority in ["主推", "推荐"]:
                main_tui.append(plan)
            else:
                backup.append(plan)

        # ── P1-2：审查-决策一致性校验（防止越权）────────────────
        # 检查决策建议是否与审查结果冲突
        consistency_issues = []
        for plan in plans:
            code = plan.code
            # 查找审查结果中的该股票
            review_stock = next((s for s in scored_stocks if s.get("code") == code), None)
            if review_stock:
                review_score = review_stock.get("composite_score", review_stock.get("score", 0))
                review_action = review_stock.get("action_advice", "")
                review_flow = review_stock.get("flow_direction", "")

                # 冲突检测1：审查建议"回避"但决策推荐买入
                if review_action == "回避" and priority in ["主推", "推荐"]:
                    consistency_issues.append({
                        "code": code, "name": plan.name,
                        "issue": f"审查建议'回避'但决策推荐'{priority}'",
                        "severity": "high"
                    })

                # 冲突检测2：审查已降级但决策仍推荐
                if review_flow == "降级" and priority in ["主推", "推荐"]:
                    consistency_issues.append({
                        "code": code, "name": plan.name,
                        "issue": f"审查已降级但决策仍推荐'{priority}'",
                        "severity": "high"
                    })

                # 冲突检测3：审查评分<60但决策推荐
                if review_score < 60 and priority in ["主推", "推荐"]:
                    consistency_issues.append({
                        "code": code, "name": plan.name,
                        "issue": f"审查评分{review_score}分<60但决策推荐'{priority}'",
                        "severity": "high"
                    })

                # P0-2026-06-04: 阈值已同步为≥75（与审查升级阈值一致）
                # 冲突检测4：审查评分60-74（黄色预警）但决策主推
                if 60 <= review_score < 75 and priority == "主推":
                    consistency_issues.append({
                        "code": code, "name": plan.name,
                        "issue": f"审查评分{review_score}分（黄色预警）但决策主推",
                        "severity": "medium"
                    })

        if consistency_issues:
            print(f"[DecisionAgent] ⚠️ 审查-决策一致性校验发现 {len(consistency_issues)} 个冲突:")
            for issue in consistency_issues:
                print(f"   - {issue['name']}({issue['code']}): {issue['issue']} [{issue['severity']}]")

            # 高严重性冲突：自动降级推荐优先级
            for issue in consistency_issues:
                if issue["severity"] == "high":
                    for plan in plans:
                        if plan.code == issue["code"]:
                            plan.priority = "备选"
                            plan.risk_notes.append(f"⚠️ 一致性校验: {issue['issue']}")

        decision_output = DecisionOutput(
            raw_text=raw_text,
            timestamp=datetime.now().isoformat(),
            consistency_issues=consistency_issues,
        )
        return DecisionResult(
            success=True,
            output=decision_output,
            plans=plans,
            main_tui=main_tui,
            backup=backup,
            no_action_reason=no_action_reason,
        )

    def _load_pools(self) -> dict:
        """使用PoolManager加载所有池数据（接近决策池已停用）"""
        pools = {}
        for name in ["快筛候选池", "重点观察池", "边缘池", "持仓池", "S级操作池"]:
            data = self.pool_manager.load_pool(name)
            pools[name] = {"stocks": data.get("stocks", [])} if data else {"stocks": []}
        return pools

    def _format_pools(self, pools: dict) -> str:
        lines = []
        for name, data in pools.items():
            stocks = data.get("stocks", [])
            if stocks:
                display = ", ".join([f"{s.get('股票名称', s.get('名称','?'))}({s.get('股票代码', s.get('代码','?'))})" for s in stocks[:5]])
                lines.append(f"- **{name}**：{display}")
        return "\n".join(lines) if lines else "（暂无池数据）"

    def _build_realtime_section(self, pools: dict, extra_codes: Optional[list] = None) -> str:
        """
        强制从腾讯API拉实时行情，注入到LLM prompt。
        解决池文件推荐买入价是历史旧值的问题。
        extra_codes: 额外需要查询行情的股票代码（如审查报告中已评分的标的）
        """
        if not self._fetch_quotes:
            return ""

        import sys
        for mod in list(sys.modules.keys()):
            if 'market_agent' in mod:
                del sys.modules[mod]
        try:
            from market_agent import fetch_quotes
        except Exception:
            return ""

        # 收集所有池的股票代码
        code_map = {}  # code_raw -> {name, rec_buy}
        for name, data in pools.items():
            for s in data.get("stocks", []):
                code = str(s.get("代码", "")).strip()
                if code.startswith(("sh", "sz")):
                    raw = code[2:]
                    prefix = code[:2]
                else:
                    raw = code
                    prefix = "sh" if raw.startswith(("6", "5", "9")) else "sz"
                if raw and raw not in code_map:
                    rec = s.get("推荐买入价", "")
                    code_map[raw] = {
                        "api": f"{prefix}{raw}",
                        "name": s.get("名称", s.get("股票名称", "")),
                        "rec_buy": rec,
                    }

        # P3修复：注入审查报告中评过分的股票，确保行情覆盖
        # （即使该股票已被审查流转移出池，仍需实时行情用于决策）
        if extra_codes:
            for raw in extra_codes:
                raw = str(raw).strip()
                if raw in code_map or not raw:
                    continue
                prefix = "sh" if raw.startswith(("6", "5", "9")) else "sz"
                code_map[raw] = {
                    "api": f"{prefix}{raw}",
                    "name": "",  # 留空，由API返回的真实名称填充
                    "rec_buy": "",
                }

        if not code_map:
            return ""

        # 批量查实时行情（分批防超长URL）
        all_quotes = {}
        codes_list = list({v["api"] for v in code_map.values()})
        for i in range(0, len(codes_list), 20):
            batch = codes_list[i:i+20]
            try:
                for item in fetch_quotes(batch):
                    all_quotes[item["代码"]] = item
            except Exception:
                pass

        # ── 涨停/跌停检测：仅在盘中交易时段启用 → 并将排除代码写到实例变量供_run_impl过滤 ──
        now = datetime.now()
        is_trading_session = (now.weekday() < 5  # 周一至周五
                              and (9 <= now.hour < 11 or 12 <= now.hour < 15))
        self._limit_up_excluded_codes = set()
        excluded_codes_by_status = {"涨停": [], "跌停": []}

        # 预扫描：收集封板标的
        for raw, info in code_map.items():
            q = all_quotes.get(raw, {})
            if not q or not q.get("现价"):
                continue
            status = q.get("交易状态", "正常")
            if is_trading_session and status in ("涨停", "跌停"):
                self._limit_up_excluded_codes.add(raw)
                excluded_codes_by_status.setdefault(status, []).append((raw, q.get("名称", info.get("name", "?")), q.get("现价", 0), q.get("涨跌幅", 0)))

        # ── 生成涨停/跌停排除说明 ──
        warn_lines = []
        if excluded_codes_by_status["涨停"]:
            warn_lines.append("⛔ **以下股票已涨停，无法买入（已从候选池移除）：**")
            for raw, name, price, chg in excluded_codes_by_status["涨停"]:
                warn_lines.append(f"- {raw} {name} 现价{price:.2f} ({chg:+.2f}%)")
        if excluded_codes_by_status["跌停"]:
            warn_lines.append("🔴 **以下股票已跌停（已从候选池移除）：**")
            for raw, name, price, chg in excluded_codes_by_status["跌停"]:
                warn_lines.append(f"- {raw} {name} 现价{price:.2f} ({chg:+.2f}%)")

        # ── 生成行情表格（排除涨停/跌停标的）──
        lines = [
            "【⚠️ 实时行情 - 决策前强制刷新】",
            f"刷新时间：{now.strftime('%H:%M:%S')}",
            "**请务必使用以下实时价格制定买入/止损/止盈方案，禁止使用旧价格！**",
        ]
        if warn_lines:
            lines.extend(["", "---", ""] + warn_lines + ["", "---"])
        lines.extend([
            "",
            "| 代码 | 名称 | 现价 | 今日涨跌 | 状态 | 推荐买点 | 偏离 |",
            "|------|------|------|---------|:----:|---------|------|",
        ])
        has_deviation = False
        for raw, info in code_map.items():
            if raw in self._limit_up_excluded_codes:
                continue  # 涨停/跌停股从主表移除
            q = all_quotes.get(raw, {})
            if not q:
                continue
            price = q.get("现价", 0)
            chg = q.get("涨跌幅", 0)
            status = q.get("交易状态", "正常")
            status_emoji = "✅" if status == "涨停" else ("🔴" if status == "跌停" else "正常")
            rec = info["rec_buy"]
            name = info["name"]
            if not name:
                name = q.get("名称", "?")
            dev_str = "—"
            if rec:
                try:
                    rec_f = float(rec)
                    dev = (price - rec_f) / rec_f * 100
                    dev_str = f"{dev:+.1f}%"
                    if abs(dev) > 3:
                        has_deviation = True
                except Exception:
                    pass
            lines.append(
                f"| {raw} | {name} | **{price:.2f}** | {chg:+.2f}% | {status_emoji} | {rec or '—'} | {dev_str} |"
            )

        if has_deviation:
            lines.insert(
                3,
                "⚠️ **警告：以下股票当前价格偏离推荐买点>3%，请务必使用实时现价计算止盈止损！**"
            )

        if excluded_codes_by_status["涨停"] or excluded_codes_by_status["跌停"]:
            self.logger.info("limit_up_excluded",
                           count_zt=len(excluded_codes_by_status["涨停"]),
                           count_dt=len(excluded_codes_by_status["跌停"]),
                           codes=list(self._limit_up_excluded_codes))
        self.logger.info("realtime_fetched", count=len(all_quotes), has_deviation=has_deviation)
        return "\n".join(lines)

    def _fetch_current_prices(self) -> dict:
        """获取各池股票当前行情，返回 {代码: 现价} 字典"""
        try:
            from market_agent import fetch_quotes, to_api
        except Exception:
            return {}

        pool_files = [
            self.root / "五池管理" / "重点观察池.json",
            self.root / "五池管理" / "快筛候选池.json",
        ]
        codes = []
        for pf in pool_files:
            if not pf.exists():
                continue
            data = self.safe_read_json(pf, {})
            for s in data.get("stocks", []):
                code = str(s.get("代码", s.get("股票代码", ""))).strip()
                if code:
                    codes.append(code)

        if not codes:
            return {}

        quotes = fetch_quotes([to_api(c) for c in codes])
        return {q["代码"]: q.get("现价", q.get("current", 0)) for q in quotes if q.get("代码")}

    def _get_market_env(self) -> str:
        """获取大盘环境（优先从共享内存读取实时数据，否则用规则估算）"""
        import json
        from pathlib import Path
        sm_file = self.root / "data" / "shared_memory.json"
        if sm_file.exists():
            try:
                with open(sm_file) as f:
                    data = json.load(f)
                if data and isinstance(data, list):
                    # 上证指数 sh000001
                    sh = next((s for s in data if s.get("代码") == "000001"), None)
                    # 创业板指 sz399006
                    cyb = next((s for s in data if s.get("代码") == "399006"), None)
                    if sh:
                        sh_chg = sh.get("涨跌幅", 0)
                        sh_price = sh.get("现价", 0)
                        sh_vol = sh.get("量比", 1)
                        sh_status = "偏强" if sh_chg > 0.5 else "偏弱" if sh_chg < -0.5 else "震荡"
                        # 估算指数点位（腾讯API不直接返回点位，但返回涨跌额）
                        # 用昨收+涨跌额估算
                        sh_prev = sh.get("昨收", sh_price)
                        idx_est = round(sh_prev / (1 + sh_chg / 100), 0) if sh_chg != 0 else sh_price

                        lines = [
                            f"- **上证指数**：{sh_status}，约{int(idx_est)}点附近，{sh_chg:+.2f}%",
                            f"  量比 {sh_vol:.2f}x {'放量' if sh_vol > 1.5 else '缩量'}",
                        ]
                        if cyb:
                            cyb_chg = cyb.get("涨跌幅", 0)
                            cyb_status = "强势" if cyb_chg > 1 else "偏弱" if cyb_chg < -1 else "震荡"
                            lines.append(f"- **创业板指**：{cyb_status}，{cyb_chg:+.2f}%")

                        # 仓位建议
                        if sh_chg > 1:
                            env = "偏多"
                            pos = "单票20-30%，总仓位50%"
                        elif sh_chg > 0:
                            env = "震荡偏强"
                            pos = "单票10-20%，总仓位30%"
                        elif sh_chg > -1:
                            env = "震荡偏弱"
                            pos = "单票5-10%，总仓位20%"
                        else:
                            env = "偏空"
                            pos = "空仓或轻仓观望"

                        lines.extend([
                            f"- **市场状态**：{env}",
                            f"- **环境评级**：{pos}",
                        ])
                        return "\n".join(lines)
            except Exception:
                pass

        # Fallback：读取今日技术面分析报告
        today = datetime.now().strftime("%Y-%m-%d")
        tech_file = self.history_dir / f"{today}_技术面分析.md"
        if tech_file.exists():
            content = safe_read_file(tech_file, default="", log_error=False)
            if content:
                # 提取大盘相关行
                lines = []
                for line in content.split("\n"):
                    if any(k in line for k in ["000001", "399006", "上证", "创业", "大盘"]):
                        lines.append(line.strip())
                if lines:
                    return "\n".join(lines[:6])

        # 最终兜底：硬编码文本
        return """- **上证指数**：震荡整理，4000-4100区间波动
- **创业板指**：创新高后回调，短期偏谨慎
- **市场状态**：分化格局，强者恒强
- **环境评级**：震荡偏强，仓位建议单票10-20%，总仓位30%"""

    # ── Level-2: 市场状态预判（结构化，决定 S 池推荐数量）───
    def _get_market_state(self) -> dict:
        """获取量化市场状态，返回 {state, sh_chg, s_pool_cap, suggestion}"""
        import json
        from pathlib import Path
        sm_file = self.root / "data" / "shared_memory.json"
        result = {"state": "震荡", "sh_chg": 0, "s_pool_cap": 3, "suggestion": "标准（P2升级：容量3）"}
        if sm_file.exists():
            try:
                with open(sm_file) as f:
                    data = json.load(f)
                if data and isinstance(data, list):
                    sh = next((s for s in data if s.get("代码") == "000001"), None)
                    if sh:
                        sh_chg = sh.get("涨跌幅", 0)
                        result["sh_chg"] = sh_chg
                        if sh_chg > 1:
                            result["state"] = "偏多"
                            result["s_pool_cap"] = 3       # P2升级：3只
                            result["suggestion"] = "积极，关注科技+券商"
                        elif sh_chg > 0:
                            result["state"] = "震荡偏强"
                            result["s_pool_cap"] = 3       # P2升级：3只
                            result["suggestion"] = "谨慎积极"
                        elif sh_chg > -1:
                            result["state"] = "震荡偏弱"
                            result["s_pool_cap"] = 2        # P2升级：2只（原1只）
                            result["suggestion"] = "防御为主，关注高股息"
                        else:
                            result["state"] = "偏空"
                            result["s_pool_cap"] = 1         # P2升级：1只（原0只）
                            result["suggestion"] = "严格风控，仅极优标的"
            except Exception:
                pass
        return result

    def _extract_scores(self, review_report: str) -> list[dict]:
        """从审查报告中提取结构化评分（委托 decision_utils.extract_scores）"""
        return extract_scores(review_report)

    def _extract_skeptic_covered_codes(self, skeptic_content: str) -> set:
        """从质疑审查报告中提取所有被审查的股票代码（双源提取，无LLM）

        解析方式：
        1. 从 JSON 代码块中提取 "code": "XXXXXX" 字段
        2. 从 markdown 文本中提取 「名称（002472）」格式作为兜底
        """
        import re
        codes = set()
        # 源1：JSON 结构段中的 code 字段（最精确）
        for m in re.finditer(r'"code"\s*:\s*"(\d{6})"', skeptic_content):
            codes.add(m.group(1))
        # 源2：markdown 正文中的「名称（代码）」格式（兜底）
        for m in re.finditer(r'[（(](\d{6})[）)]', skeptic_content):
            codes.add(m.group(1))
        return codes

    def _format_scored_stocks(self, stocks: list[dict]) -> str:
        """格式化评分结构供LLM参考"""
        if not stocks:
            return ""
        lines = []
        for s in stocks[:10]:  # 最多10只
            flag = "✅" if s["score"] >= 70 else "🟡" if s["score"] >= 60 else "🔴"
            passed = "通过审查" if s["passed"] else "待观察"
            lines.append(f"- {flag} {s['name']}({s['code']}) 综合评分:{s['score']}分 [{passed}]")
        return "\n".join(lines)

    def _record_to_evo(self, scored_stocks: list[dict], decision_result: str,
                       review_report: str = "", pools: dict = None):
        """P1-3: 将决策记录写入复盘进化模块（含可验证假设）
        通过 TrackRecorder 委托，传递 hypothesis extractor 引用。
        """
        self.track_recorder.record_to_evo(
            scored_stocks, decision_result,
            review_report=review_report, pools=pools,
            hypothesis_extractor=self._extract_hypothesis,
            hypothesis_enhancer=self._enhance_hypothesis_from_decision,
            logger=self.logger,
        )

    def _update_s_pool(self, decision_result: str, scored_stocks: list = None):
        """从决策报告提取【主推】标的，写入S级操作池（容量≤2，带入场价记录 + 历史累积）
        通过 PoolUpdater 委托。Level-2：根据市场状态动态限制推荐数量。
        注意：截断不影响已保存到磁盘的决策报告（报告保留原始 LLM 输出作为参考）。
        """
        # Level-2：根据市场状态动态限制推荐数量
        market_state = self._market_state if hasattr(self, '_market_state') else {"s_pool_cap": 2}
        max_s_count = market_state.get("s_pool_cap", 2)
        # 截断决策结果中的【主推】数量
        import re
        matches = re.findall(r"【主推】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]", decision_result)
        if len(matches) > max_s_count:
            # 只保留前 max_s_count 个【主推】，其余替换为【备选】
            lines = decision_result.split("\n")
            new_lines = []
            found = 0
            for line in lines:
                if "【主推】" in line:
                    if found < max_s_count:
                        new_lines.append(line)
                        found += 1
                    else:
                        # 降级为备选
                        new_lines.append(line.replace("【主推】", "【备选】"))
                else:
                    new_lines.append(line)
            decision_result = "\n".join(new_lines)
        # 传入 scored_stocks 供防线一/三校验评分
        self.pool_updater.update_s_pool(decision_result, scored_stocks=scored_stocks or [])
        self.logger.pool_operation("S级操作池", "sync", count=0)

    def _build_empty_decision(self, today: str, pools: dict,
                               market_env: str, reason: str,
                               yellow_alerts: list = None) -> dict:
        """二审制Gate：所有标的被拦截时生成空仓决策报告（委托 decision_utils.build_empty_decision）"""
        report = build_empty_decision(today, pools, market_env, reason, yellow_alerts)
        out_file = self.history_dir / f"{today}_决策报告.md"
        self.safe_write_text(out_file, report)
        self.logger.info("empty_decision_gate", reason=reason)
        return {"success": True, "report": report, "saved_to": str(out_file), "empty_decision": True}

    def _check_s_pool_overlap(self, new_stocks: list):
        """P1-3：检查S级操作池主推标的是否已在其他流转池中（只记录，不阻止写入）
        通过 PoolUpdater 委托。
        """
        self.pool_updater._check_s_pool_overlap(new_stocks)

    def _extract_logic_snippet(self, name: str, decision_result: str) -> str:
        """提取该股票决策报告中的核心逻辑（1-2句）
        通过 PoolUpdater 委托。
        """
        return self.pool_updater._extract_logic_snippet(name, decision_result)

    def _extract_hypothesis(self, code: str, name: str, review_report: str,
                            pools: dict = None) -> tuple[str, str]:
        """从审查报告中提取该股票的核心假设（0次LLM，纯正则）

        优先从五池核心逻辑字段获取（最可靠）；
        退而从审查报告的股票章节提取关键词；
        最后用股票名称+市场主线生成兜底假设。
        """
        # ── 策略1：从五池核心逻辑获取（最可靠）──────────
        if pools:
            for pool_name, pool_data in pools.items():
                stocks = pool_data if isinstance(pool_data, list) else pool_data.get("stocks", [])
                if not isinstance(stocks, list):
                    continue
                for s in stocks:
                    s_code = s.get("股票代码") or s.get("代码", "")
                    if s_code == code:
                        core_logic = s.get("核心逻辑", "").strip()
                        if core_logic:
                            # 截取前100字作为假设
                            hypothesis = core_logic[:100]
                            expected_logic = f"核心逻辑：{core_logic[:80]}"
                            return hypothesis, expected_logic

        # ── 策略2：从审查报告提取（支持多种格式）────────
        if review_report:
            import re

            # 尝试多种标题格式
            patterns = [
                rf"##\s*{code}\s*[（(]?\s*{name}[）)]?",   # ## 600118 中国卫星
                rf"##\s*{name}\s*[（(]?\s*{code}[）)]?",   # ## 中国卫星 600118
                rf"##\s*{code}\s*",                          # ## 600118 (标题无名称)
                rf"{code}[^\n]*{name}",                      # 行内：600118 中国卫星
                rf"{name}[^\n]*{code}",                      # 行内：中国卫星 600118
            ]

            section = None
            for pat in patterns:
                m = re.search(pat, review_report)
                if m:
                    # 找到后，截取该区域（前后500字）
                    start = max(0, m.start() - 50)
                    end = min(len(review_report), m.end() + 500)
                    section = review_report[start:end]
                    break

            if not section:
                # 全局扫描：报告任意位置提到该股票代码+名称
                full_m = re.search(
                    rf"(?:{code}|{name}).{{0,200}}?(?:综合评分|信心度|驱动|逻辑|流转)",
                    review_report, re.DOTALL
                )
                if full_m:
                    section = full_m.group(0)

            if section:
                lines = section.split("\n")
                hypothesis_parts = []
                logic_parts = []

                for line in lines:
                    line = line.strip()
                    if not line or line.startswith("##") and len(line) < 5:
                        continue
                    # 核心驱动类关键词
                    if any(k in line for k in ["核心驱动", "驱动因素", "驱动逻辑",
                                                  "驱动验证", "题材", "政策利好"]):
                        col = re.split(r"[:：]", line, 1)
                        if len(col) > 1 and len(col[1].strip()) > 2:
                            hypothesis_parts.append(col[1].strip()[:80])
                    # 逻辑支撑类关键词
                    if any(k in line for k in ["逻辑支撑", "预期", "走势", "空间",
                                                  "量能", "位置分析"]):
                        col = re.split(r"[:：]", line, 1)
                        if len(col) > 1 and len(col[1].strip()) > 2:
                            logic_parts.append(col[1].strip()[:80])

                hypothesis = "；".join(hypothesis_parts[:2])
                expected_logic = "→".join(logic_parts[:3])

                if hypothesis or expected_logic:
                    return hypothesis[:200], expected_logic[:200]

        # ── 兜底：基于股票名称生成通用假设 ─────────────
        # （表明假设已被记录，避免日志中出现"无假设"）
        hypothesis = f"{name}：存在潜在驱动逻辑，待进一步验证"
        expected_logic = "基本面+技术面支撑，等待催化剂验证"
        return hypothesis[:200], expected_logic[:200]

    def _enhance_hypothesis_from_decision(self, code: str, name: str, decision_result: str) -> str:
        """从决策报告中提取该股票的决策理由，用于增强假设"""
        import re
        # 匹配决策报告中该股票的段落
        # 格式：## 双环传动(002472) 或 ### 【主推】双环传动（002472）
        patterns = [
            rf"##\s*{name}\s*[（(]?\s*{code}[）)]?",   # ## 双环传动(002472)
            rf"###?\s*[【【]?[主推]?[】]?\s*{name}\s*[（(]?\s*{code}[）)]?",  # ### 【主推】双环传动（002472）
            rf"{code}[^\\n]*{name}[^\\n]*?(?:驱动|逻辑|理由)",
        ]

        for pat in patterns:
            m = re.search(pat, decision_result)
            if m:
                start = max(0, m.start())
                end = min(len(decision_result), m.end() + 300)
                section = decision_result[start:end]
                # 提取关键短语：匹配"核心逻辑"/"驱动"/"逻辑支撑"等行
                for line in section.split("\n"):
                    line = line.strip()
                    if any(kw in line for kw in ['核心逻辑', '逻辑支撑', '驱动']):
                        # 提取冒号后的内容
                        if '：' in line:
                            return line.split('：', 1)[1][:100]
                        elif ':' in line:
                            return line.split(':', 1)[1][:100]
        return ""

    def _generate_summary(self, today: str):
        """生成四段闭环汇总"""
        files = {
            "宏观前置分析": f"{today}_宏观前置分析.md",
            "快筛报告": f"{today}_快筛报告.md",
            "审查报告": f"{today}_审查报告.md",
            "决策报告": f"{today}_决策报告.md",
        }

        content = f"# 【四段闭环汇总】{today}\n\n"
        for name, fname in files.items():
            f = self.history_dir / fname
            if f.exists():
                content += f"\n## {name}\n\n"
                content += f"📄 已生成：{fname}\n\n"

        summary_file = self.history_dir / f"{today}_四段闭环汇总.md"
        self.safe_write_text(summary_file, content)


if __name__ == "__main__":
    agent = DecisionAgent()
    result = agent.run()
    if result["success"]:
        print(f"✅ 决策完成")
        print(f"📄 保存: {result['saved_to']}")
        print("\n" + "=" * 40)
        print(result["report"][:800])