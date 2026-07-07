#!/usr/bin/env python3
"""
Review Agent - 审查 Agent（重构版，双盲审查）
对候选池股票做四维深度审查
1次LLM调用

设计原则：双盲机制
- 此Agent不知道快筛阶段推荐的理由（只看代码和名称）
- 避免"因为推荐所以找理由验证"的确认偏误
- 只输出：综合评分 + 信心度 + 流转方向

继承BaseAgent获得：
- 统一的LLM调用（指数退避重试）
- 安全文件读写
- 统计跟踪
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from base_agent import BaseAgent, build_agent_system_prompt
from logger import StructuredLogger
from pool_manager import PoolManager
from review_scorer import OverheatDetector
from schemas import ReviewOutput, ReviewResult, StockReview, DimensionScore
from schemas import REVIEW_SCHEMA
from thresholds import AUTO_DOWNGRADE_SCORE, HARD_DOWNGRADE_SCORE, SCORE_C_LEVEL, YELLOW_ALERT_MIN, DECISION_MIN_SCORE

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

from market_agent import fetch_quotes, to_api


ROLE_PROMPT = """你是一个独立的股票审查专家，负责对候选股票做四维深度审查。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【深度思考协议】审查前必须先想清楚再输出
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

在看到每只股票时，按以下三步思考，**想清楚再写结论**：

**第一步：基本面定性（这只股票是干什么的？）**
- 所处行业是当前主线吗？
- 近期有没有业绩拐点或政策利好？
- 市值大小是否适合短线操作？

**第二步：四维逐条打分（不要直接拍脑袋）**
| 维度 | 核心问题 | 打分依据 |
|------|---------|---------|
| 驱动验证（25%）| 有没有实质利好驱动？ | 政策/数据/事件三重验证 |
| 位置分析（35%）| 价格在低位还是高位？ | 相对历史和板块位置——**处于52周或年内高位(>80%分位)的至少扣10分** |
| 量能判断（20%）| 有没有资金在参与？ | 成交量/换手率异动 |
| 风险扫描（20%）| 有没有一票否决的风险？ | ST/亏损/退市/解禁/商誉 |

**第三步：排坑检查（先排除坏股票）**
⚠️ 有一项直接淘汰，不给高分：
- ST或*ST字样
- 连续两年亏损
- 退市风险警示
- 解禁盘超过流通盘20%
- 商誉占净资产超50%
- 主力已明显出货（高位放量大阴线）

完成三步思考后，再输出结构化结论。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【你的工作方式】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

你的工作方式：审查+参考驱动
- 你看到股票代码、名称、以及快筛推荐的驱动逻辑
- 你独立判断它的价值，但可以参考驱动级别作为加分项
- S级驱动的股票，四维打分可额外+5分（但不超过100分）
- B级或C级驱动的股票，四维打分额外-5分

### 输出模板

### 流转方向
→ 升级/保留/降级 → [目标池名称]
```

⚠️ **硬性要求**：
- 禁止输出任何开场白
- 每只股票总字数不超过200字
- **≥75分升级，65-74分保留，55-64分降级，<55分淘汰**

评分标准：
- 90-100：极佳机会，强烈建议关注
- 75-89：可以关注，逻辑通顺
- 65-74：谨慎观察，需等待更好时机（黄色预警区）
- 55-64：建议暂缓
- <55：建议淘汰

流转方向：
- **≥75：升级→重点观察池**
- 65-74：保留候选池（黄色预警，需进一步观察）
- 55-64：降级→边缘池（观察区）
- <55：淘汰→移出候选池

输出格式（禁止任何开场白，直接输出结构化结论）：
```markdown
## [代码] 股票名称

### 🔍 深度思考（简短，不超过3行）
**基本面**：...
**四维打分**：...
**排坑**：...

### 四维审查结果
| 维度 | 评分(0-100) | 说明 |
|------|-------------|------|
| 驱动验证 | XX | ... |
| 位置分析 | XX | ... |
| 量能判断 | XX | ... |
| 风险扫描 | XX | ... |
| **综合评分** | **XX** | **信心度描述** |

### 流转方向
→ 升级/保留/降级 → [目标池名称]
```

⚠️ **硬性要求**：
- 禁止输出任何开场白
- 每只股票总字数不超过200字
"""


USER_PROMPT_TEMPLATE = """请对以下候选股票池进行四维深度审查（审查+参考驱动模式）：

候选池股票：
{candidate_stocks}

{realtime_section}

今日宏观背景（请在评分时考虑市场环境影响）：
{market_context}

请对每只股票独立评分，并给出流转建议。注意：
|- 当前市场状态：{market_state}
|- 市场状态决定了评分尺度，请遵循以下指导：
|   · 偏多/震荡偏强（强市）：位置评分可适当放宽（趋势延续性优先，不因短期高位扣太多分），驱动逻辑可信度加分
|   · 震荡：中性评分
|   · 震荡偏弱/偏空（弱市）：严格执行位置风险扣分，驱动逻辑必须可证伪
|- S级驱动的股票可适当加分（但不超过100分）
|- B级或C级驱动的股票应适当扣分
|- 评分≥75分才升级，65-74分保留，55-64分降级，<55分淘汰
|- 重要：弱市环境下（震荡偏弱/偏空）评分应比牛市时系统性降低5-8分"""


class ReviewAgent(BaseAgent):
    """审查 Agent（双盲，继承BaseAgent）"""

    def __init__(self, agent_name: str = "ReviewAgent"):
        super().__init__(agent_name)
        self.history_dir = self.root / "data" / "历史记录"
        self.pool_dir = self.root / "五池管理"
        self.pool_manager = PoolManager(self.pool_dir)
        self.logger = StructuredLogger("ReviewAgent")

    def run(self, screen_report: Optional[str] = None, wake_ctx: str = "") -> dict:
        """执行审查"""
        with self.logger.agent_action("run"):
            return self._run_impl(screen_report, wake_ctx)

    def _run_impl(self, screen_report: Optional[str] = None, wake_ctx: str = "") -> dict:
        today = datetime.now().strftime("%Y-%m-%d")

        # 读取快筛报告获取候选股票
        if screen_report is None:
            screen_file = self.history_dir / f"{today}_快筛报告.md"
            if screen_file.exists():
                screen_report = self.safe_read_text(screen_file)

        if not screen_report:
            return {"success": False, "error": "没有找到快筛报告"}

        # 读取宏观背景
        macro_file = self.history_dir / f"{today}_宏观前置分析.md"
        macro_context = self.safe_read_text(macro_file, "（暂无宏观数据）")

        # 读取候选池
        pool_file = self.pool_dir / "快筛候选池.json"
        if pool_file.exists():
            data = self.safe_read_json(pool_file, {})
            raw = data.get("stocks", [])
            # P0-快筛漏检修复：过滤未覆盖的快筛历史滞留股
            fs_hist = data.get("_fast_screen_history", {})
            if fs_hist:
                _before = len(raw)
                raw = [s for s in raw if fs_hist.get(
                    str(s.get("代码") or s.get("股票代码", ""))
                ) == today]
                if len(raw) < _before:
                    print(f"[ReviewAgent] ⏭ 过滤 {_before - len(raw)} 只快筛未覆盖滞留股（近次快筛非今日）")
        else:
            raw = []

        # P1-1: 行情只拉一次，复用给两个方法
        qmap = {}
        if raw:
            qmap = self._fetch_quotes_for_stocks(raw)

        # ── P1-3: 预计算因子信号（第5维）──────────────────────
        factor_map = {}
        if raw:
            try:
                from market_agent import calculate_qlib_factors
                for s in raw:
                    code = str(s.get("代码") or s.get("股票代码", "")).strip()
                    if code:
                        factors = calculate_qlib_factors(s)
                        factor_map[code] = factors  # 存全量因子, 供ML评分使用
                        sig = factors.get("factor_signal", 0)
                        if sig >= 3:
                            print(f"[ReviewAgent] 📈 因子信号: {s.get('名称','?')}({code}) {sig}/6")
                if factor_map:
                    good = sum(1 for v in factor_map.values() if v.get("factor_signal", 0) >= 3)
                    print(f"[ReviewAgent] 📈 因子信号预计算: {len(factor_map)} 只, {good} 只达标(≥3/6)")
            except Exception as e:
                print(f"[ReviewAgent] ⚠️ 因子计算异常: {e}")

        # 注入实时行情（表格）
        realtime_section = self._build_realtime_section(raw, qmap)

        if raw:
            candidate_stocks = self._format_stocks_with_quote(raw, qmap)
        else:
            candidate_stocks = self._extract_from_report(screen_report) if screen_report else "（无候选股票）"

        if not candidate_stocks.strip() or candidate_stocks == "（无候选股票）":
            return {"success": False, "error": "候选池为空"}

        # LLM 审查（含市场状态上下文）
        self.logger.llm_call("review_stocks", tokens=len(candidate_stocks))
        market_state = self._get_market_state()
        market_state_label = f"{market_state.get('state','震荡')}（上证{market_state.get('sh_chg',0):+.2f}%）"
        user_prompt = USER_PROMPT_TEMPLATE.format(
            candidate_stocks=candidate_stocks,
            realtime_section=realtime_section,
            market_context=macro_context[:500],
            market_state=market_state_label,
        )
        result = self.call_llm(
            user_prompt,
            system=build_agent_system_prompt(ROLE_PROMPT, "ReviewAgent", extra_context=wake_ctx),
            max_tokens=4000
        )

        # 格式化报告
        report = f"""# 【审查报告】{today}

━━━━━━━━━━━━━━━━

## 候选池审查结果

### 候选池现有：{self._count_stocks(candidate_stocks)} 只

## 深度审查结果

{self._format_review_result(result)}

---

{self._generate_pool_updates(result)}

---
审查执行时间：{datetime.now().strftime('%H:%M')}

{self._generate_pool_summary()}
"""

        # 保存
        out_file = self.history_dir / f"{today}_审查报告.md"
        self.safe_write_text(out_file, report)

        # 解析后处理结果（用于池更新，不走LLM原始文本）
        parsed_result = self._parse_review_result_v2(result)
        
        # 更新池（使用后处理的upgrades/demotions，非LLM原始文本）
        self._apply_pool_updates(result, parsed_result.upgrades, parsed_result.demotions)

        # ── v5.91: 重点观察池评估结果追加到审查报告.md ──────────
        # 批量评估升级后的重点池数据只写入了池JSON，未同步到审查报告.md
        # 导致决策层读审查报告.md时看不到新升级标的的评分
        try:
            from pool_manager import PoolManager
            pm = PoolManager()
            kw_pool = pm.load_pool("重点观察池")
            kw_stocks = kw_pool.get("stocks", []) if isinstance(kw_pool, dict) else []
            if kw_stocks:
                appendix_lines = ["\n\n---\n",
                                   "## 📋 重点观察池最新评估\n",
                                   "| 股票 | 综合分 | 信心度 | 现价 | 涨跌 | 换手 | 量比 | 核心逻辑 |\n",
                                   "|------|--------|--------|------|------|------|------|----------|\n"]
                for s in kw_stocks:
                    name = s.get("名称", "?")
                    code = s.get("代码", "")
                    score = s.get("综合分", "—")
                    conf = s.get("信心度", "—")
                    price = s.get("今日收盘", "—")
                    chg = s.get("今日涨跌", "—")
                    tr = s.get("换手率", "—")
                    vr = s.get("量比", "—")
                    logic = s.get("核心逻辑", "")[:30]
                    appendix_lines.append(f"| {name}({code}) | {score} | {conf} | {price} | {chg} | {tr}% | {vr} | {logic} |\n")
                appendix = "".join(appendix_lines)
                # 追加到已保存的审查报告.md
                from safe_file_utils import safe_append_file
                success = safe_append_file(str(out_file), appendix)
                if not success:
                    logger.warning(f"[ReviewAgent] 追加审查报告失败: {out_file}")
                # 同时更新内存中的 report，供上游主流程返回
                report += appendix
                print(f"[ReviewAgent] ✅ 重点观察池 {len(kw_stocks)} 只评估结果已同步到审查报告.md")
        except Exception as e:
            print(f"[ReviewAgent] ⚠️ 重点观察池评估写入审查报告失败: {e}")

        self.logger.info("review_complete",
                        stocks_reviewed=self._count_stocks(candidate_stocks),
                        saved_to=str(out_file),
                        stats=self.get_stats())

        # ── 构建 ReviewResult（新增 schema 结构化输出）─────────────
        review_result = self._parse_review_result_v2(result)

        # ── P1-3: 因子信号后处理加分（第5维）──────────────────
        if factor_map and review_result.stocks:
            for sr in review_result.stocks:
                fd = factor_map.get(sr.code, {})
                sig = fd.get("factor_signal", 0) if isinstance(fd, dict) else 0
                if sig >= 3:
                    bonus = min(round(sig * 0.5, 0), 3)
                    sr.composite_score = min(sr.composite_score + bonus, 100)
                    sr.core_logic += f" | 📈 因子信号{sig}/6，加{bonus:.0f}分"
                    print(f"[ReviewAgent] 📈 因子信号加分: {sr.name}({sr.code}) {sig}/6 → +{bonus:.0f}分")

        # ═══ P0-修复（2026-06-10）：评分膨胀 — 市场状态评分通缩 ═══
        market_state = self._get_market_state()
        DEFLATION_MAP = {"偏空": 8, "震荡偏弱": 5, "震荡": 3, "震荡偏强": 0, "偏多": 0}
        deflation = DEFLATION_MAP.get(market_state.get("state", "震荡"), 3)
        if deflation > 0:
            deflated_count = 0
            for sr in review_result.stocks:
                original = sr.composite_score
                sr.composite_score = max(40, sr.composite_score - deflation)
                sr.core_logic += f" | 📉 市场{market_state.get('state','?')}通缩-{deflation}分({original}→{sr.composite_score})"
                deflated_count += 1
            print(f"[ReviewAgent] 📉 市场状态[{market_state.get('state','?')}] 评分通缩: {deflated_count}只各减{deflation}分")
        # 因子信号加分在弱市中减半
        if market_state.get("state") in ["偏空", "震荡偏弱"] and factor_map and review_result.stocks:
            for sr in review_result.stocks:
                fd = factor_map.get(sr.code, {})
                sig = fd.get("factor_signal", 0) if isinstance(fd, dict) else 0
                if sig >= 3 and sr.composite_score > 0:
                    original_bonus = min(round(sig * 0.5, 0), 3)
                    # 弱市减半加分
                    half_bonus = max(0, original_bonus // 2)
                    diff = original_bonus - half_bonus
                    sr.composite_score = max(40, sr.composite_score - diff)
                    sr.core_logic = sr.core_logic.replace(f"因子信号{sig}/6，加{original_bonus:.0f}分",
                                                           f"因子信号{sig}/6，弱市减半加{half_bonus:.0f}分")
                    print(f"[ReviewAgent] 📉 弱市因子信号减半: {sr.name}({sr.code}) +{original_bonus:.0f}→+{half_bonus:.0f}分")
        # ════════════════════════════════════════════════════════════════

        # ═══ P2-修复（2026-06-10）：QualityGate 上移 — 审查阶段历史表现检查 ═══
        try:
            from quality_gate import QualityGate
            qg = QualityGate(self.root)
            for sr in review_result.stocks:
                gate_ret = qg.check(
                    name=sr.name, code=sr.code, score=sr.composite_score,
                    market_state=market_state,
                )
                if not gate_ret["passed"]:
                    original = sr.composite_score
                    sr.composite_score = gate_ret["adjusted_score"]
                    sr.core_logic += f" | 🚫 历史质检: {gate_ret['reason']}"
                    # 如果通缩后低于SCORE_C_LEVEL分→强制降级
                    if sr.composite_score < SCORE_C_LEVEL:
                        sr.flow_direction = "降级"
                        sr.target_pool = "边缘池"
                    print(f"[ReviewAgent] 🚫 质检拦截: {sr.name}({sr.code}) {original}→{sr.composite_score} {gate_ret['reason']}")

            # ML评分低信心标记（非阻塞红旗，ML<45且LLM≥75时标注背离）
            for sr in review_result.stocks:
                if sr.ml_score is not None and sr.ml_win_prob is not None and sr.ml_score < 45 and sr.composite_score >= 75:
                    sr.core_logic += f" | ⚠️ ML{sr.ml_score}分偏低(胜{sr.ml_win_prob*100:.0f}%)，与LLM{sr.composite_score}分背离"
                    print(f"[ReviewAgent] ⚠️ ML低信心: {sr.name}({sr.code}) LLM{sr.composite_score}→ML{sr.ml_score}分 胜率{sr.ml_win_prob*100:.0f}%")
                    # ML评分降级：LLM高分但ML低分，说明模型不认可
                    if sr.flow_direction == "升级":
                        sr.flow_direction = "降级"
                        sr.target_pool = "边缘池"
                        sr.core_logic += f" | 📉 ML{sr.ml_score}分<45，LLM高分背离，降级"
                        print(f"[ReviewAgent] 📉 ML降级: {sr.name}({sr.code}) LLM{sr.composite_score}分→ML{sr.ml_score}分背离，降入边缘池")
        except ImportError:
            pass
        # ═══════════════════════════════════════════════════════════════════════

        # ═══ ML评分前置拦截：升级重点池前先过ML阈值 ═══════════════
        _ml_blocked = []
        for sr in review_result.stocks:
            if sr.ml_score is not None and sr.ml_score < 45 and sr.flow_direction == "升级":
                sr.flow_direction = "降级"
                sr.target_pool = "边缘池"
                sr.core_logic += f" | 🚫 ML{sr.ml_score}分<45，前置拦截"
                _ml_blocked.append(sr)
                print(f"[ReviewAgent] 🚫 ML前置拦截: {sr.name}({sr.code}) LLM{sr.composite_score}分→ML{sr.ml_score}分，禁止升级入池")
        if _ml_blocked:
            parsed_result.demotions.extend(_ml_blocked)
            self._apply_pool_updates(result, parsed_result.upgrades, parsed_result.demotions)
            print(f"[ReviewAgent] 🧹 ML前置拦截: {len(_ml_blocked)} 只低ML分标被阻止升级")
        # ═══════════════════════════════════════════════════════════════════════

        # ═══ ML评分模型 — 并列显示（2026-06-11）═══════════════════════════
        try:
            from scripts.ml_scorer import predict_ml_score
            for sr in review_result.stocks:
                fd = factor_map.get(sr.code, {})
                detail = fd.get("factor_details", {}) if isinstance(fd, dict) else {}
                if detail and isinstance(detail, dict):
                    ml_factors = {
                        "ma5_div": round((detail.get("factor_ma5", 1) - 1) * 100, 2),
                        "ma10_div": round((detail.get("factor_ma10", 1) - 1) * 100, 2),
                        "ret5": round(detail.get("factor_ret5", 0) * 100, 2),
                        "ret20": round(detail.get("factor_ret20", 0) * 100, 2),
                        "vol20": detail.get("factor_vol20", 0),
                        "vol_ratio": detail.get("factor_turn", 1),
                        "day_range": detail.get("day_range", 0),
                        "ma20_pos": detail.get("ma20_pos", 0),
                    }
                    ml_result = predict_ml_score(ml_factors, llm_score=sr.composite_score)
                    ml_score = ml_result["ml_score"]
                    win_prob = ml_result["win_prob"]
                    sr.ml_score = ml_score
                    sr.ml_win_prob = win_prob
                    sr.core_logic += f" | 🤖 ML{ml_score}分(胜{win_prob*100:.0f}%)"
                    print(f"[ReviewAgent] 🤖 ML评分: {sr.name}({sr.code}) LLM{sr.composite_score}→ML{ml_score}分 胜率{win_prob*100:.0f}%")
        except Exception as e:
            print(f"[ReviewAgent] ⚠️ ML评分异常: {e}")
        # ════════════════════════════════════════════════════════════════════

        # ═══ P0-降级延迟修复：评分调整后重新检查硬性降级阈值 ═══
        # 因子加分/市场通缩/质检调整后，部分标的评分可能降至60以下
        # 但原始flow_direction未同步更新，导致低分标的未降级
        _extra_demotions = []
        for sr in review_result.stocks:
            if sr.composite_score < AUTO_DOWNGRADE_SCORE and sr.flow_direction != "降级":
                sr.flow_direction = "降级"
                sr.target_pool = "边缘池"
                sr.core_logic += f" | 🔴 评分调整后硬性降级：{sr.composite_score}分<{AUTO_DOWNGRADE_SCORE}分阈值"
                _extra_demotions.append(sr)
                print(f"[ReviewAgent] 🔴 评分调整后硬性降级: {sr.name}({sr.code}) {sr.composite_score}分<{AUTO_DOWNGRADE_SCORE}分 → 边缘池")
        if _extra_demotions:
            parsed_result.demotions.extend(_extra_demotions)
            self._apply_pool_updates(result, parsed_result.upgrades, parsed_result.demotions)
        # ═══════════════════════════════════════════════════════════════════════

        # ═══ P0-降级延迟修复：候选池残留低分股清理 ═══
        # 扫描候选池中仍存在且评分<60的标的，强制迁入边缘池
        _pool_cleanup = []
        for sr in review_result.stocks:
            if sr.composite_score is not None and sr.composite_score < AUTO_DOWNGRADE_SCORE and sr.flow_direction not in ("降级", "淘汰"):
                sr.flow_direction = "降级"
                sr.target_pool = "边缘池"
                sr.core_logic += f" | 🔴 残留低分清理：{sr.composite_score}分<{AUTO_DOWNGRADE_SCORE}分阈值"
                _pool_cleanup.append(sr)
                print(f"[ReviewAgent] 🔴 残留低分清理: {sr.name}({sr.code}) {sr.composite_score}分<{AUTO_DOWNGRADE_SCORE}分 → 边缘池")
        if _pool_cleanup:
            parsed_result.demotions.extend(_pool_cleanup)
            self._apply_pool_updates(result, parsed_result.upgrades, parsed_result.demotions)
            print(f"[ReviewAgent] 🧹 候选池清理: {len(_pool_cleanup)} 只低分股已降级")

        # ML评分附录 — 追加到审查报告（2026-06-11）
        try:
            import re as _re
            ml_lines = ["\n---\n## 🤖 ML评分 vs LLM评分对比\n",
                         "注：LLM综合分可能因过热检测（大涨>8%扣分）等规则进行了调整，与审查报告正文的原始评分可能不一致。\n",
                         "| 股票 | LLM综合分 | ML评分 | 上涨概率 | 核心因子 |\n",
                         "|------|:--------:|:-----:|:-------:|----------|\n"]
            for sr in review_result.stocks:
                fd = factor_map.get(sr.code, {})
                detail = fd.get("factor_details", {}) if isinstance(fd, dict) else {}
                feat_parts = []
                if detail and isinstance(detail, dict):
                    for mk, mv in [("factor_vol20","波动"), ("day_range","振幅"), ("factor_ret20","20日涨")]:
                        val = detail.get(mk, 0)
                        if val:
                            feat_parts.append(f"{mv}{val:.1f}")
                feat_str = " ".join(feat_parts[:3]) if feat_parts else "—"
                m = _re.search(r'ML(\d+)分\(胜(\d+)%\)', sr.core_logic)
                ml_score = m.group(1) if m else "—"
                win_pct = m.group(2) if m else "—"
                ml_lines.append(f"| {sr.name}({sr.code}) | {sr.composite_score} | {ml_score} | {win_pct}% | {feat_str} |\n")
            if len(ml_lines) > 3:
                ml_appendix = "".join(ml_lines)
                from safe_file_utils import safe_append_file
                success = safe_append_file(str(out_file), ml_appendix)
                if success:
                    print(f"[ReviewAgent] 🤖 ML评分对比表已追加到审查报告（{len(ml_lines)-3} 只）")
                report += ml_appendix
        except Exception as e:
            print(f"[ReviewAgent] ⚠️ ML评分附录异常: {e}")

        # ── P2-3：闭环追踪记录 ──────────────────────────────
        from closed_loop_tracker import ClosedLoopTracker
        tracker = ClosedLoopTracker()
        try:
            for stock in review_result.stocks:
                tracker.record_review(
                    code=stock.code,
                    name=stock.name,
                    score=stock.composite_score,
                    level=stock.driver_level,
                    flow_direction=stock.flow_direction,
                    target_pool=stock.target_pool,
                    action_advice=stock.action_advice or "",
                )
        except Exception as e:
            self.logger.warning("closed_loop_review_fail", error=str(e))

        # 保留旧 dict 返回格式供主流程兼容
        return {
            "success": True,
            "report": report,
            "raw_result": result,
            "saved_to": str(out_file),
            "review_result": review_result,  # 新增：结构化结果
            "upgrades": [(s.code, s.name) for s in review_result.upgrades],
            "demotions": [(s.code, s.name) for s in review_result.demotions],
        }

    def _generate_pool_summary(self) -> str:
        """生成五池状态列表（供盟主每日查阅）"""
        from pool_manager import PoolManager
        pm = PoolManager()
        lines = ["## 📊 五池当前状态\n"]

        pool_names = ["快筛候选池", "重点观察池", "边缘池", "持仓池"]
        total = 0
        for name in pool_names:
            stocks = pm.get_stocks(name)
            count = len(stocks)
            total += count
            if count == 0:
                lines.append(f"- **{name}**：0只")
            else:
                stock_list = ", ".join(
                    f"{s.get('股票名称', s.get('名称', '?'))}({s.get('股票代码', s.get('代码', '?'))})"
                    for s in stocks[:8]
                )
                suffix = f"... 等{count}只" if count > 8 else ""
                lines.append(f"- **{name}**（{count}只）：{stock_list}{suffix}")

        lines.append(f"\n**合计：{total} 只**")
        return "\n".join(lines)

    def _format_stocks(self, stocks: list) -> str:
        if not stocks:
            return "（候选池为空）"
        lines = []
        for s in stocks:
            code = s.get("股票代码", s.get("代码", "?"))
            name = s.get("股票名称", s.get("名称", "?"))
            driver = s.get("驱动级别", "")
            driver_str = f" [驱动:{driver}]" if driver else ""
            lines.append(f"- {code} {name}{driver_str}")
        return "\n".join(lines)

    def _extract_from_report(self, report: str) -> str:
        """从快筛报告中提取股票"""
        stocks = re.findall(r"(\d{6})\s*[（(]?([\u4e00-\u9fa5]{2,6})[）)]?", report)
        if not stocks:
            return "（未能提取到股票）"
        return "\n".join([f"- {code} {name}" for code, name in stocks[:10]])

    @staticmethod
    def _batch_enrich_key_watch(stocks: list) -> dict:
        """
        批量评估重点观察池股票的买入区/止损/目标（P1-2：一次LLM调用搞定所有）。
        返回 {代码: 评估dict}。
        """
        import re

        if not stocks:
            return {}

        # 构建批量股票行情列表
        stock_lines = []
        codes_raw = [str(s.get("代码") or s.get("股票代码", "")).strip() for s in stocks]
        api_codes = [to_api(c) for c in codes_raw if c]

        qmap = {}
        try:
            for item in fetch_quotes(api_codes):
                qmap[item["代码"]] = item
        except Exception:
            pass

        for s in stocks:
            code = str(s.get("代码") or s.get("股票代码", "")).strip()
            name = s.get("名称", s.get("股票名称", "?"))
            q = qmap.get(code, {})
            price = q.get("现价", 0)
            chg = q.get("涨跌幅", 0)
            pe = q.get("市盈率_TTM", "—")
            turnover = q.get("换手率", 0)
            stock_lines.append(
                f"- {name}({code}) | 现价:{price}({chg:+.2f}%) | PE:{pe} | 换手:{turnover:.2f}%"
            )

        if not stock_lines:
            return {}

        stocks_text = "\n".join(stock_lines)

        prompt = f"""## 重点观察池批量建仓前评估

请对以下股票批量评估建仓前参考（短线风格）：

{stocks_text}

评估要求：结合大盘环境、行业景气度、技术形态，给出每只股票的：
- 推荐买入价（现价附近或回调支撑位，具体价格）
- 止损触发价（买入价下方5-8%，跌破代表逻辑失效需放弃）
- 第一目标价（距买入价约+10%，具体价格）
- 第二目标价（距买入价约+20%，具体价格）
- 操作建议（买入/观望/回避）

直接输出结论，不需要开场白："""
        # 追加每只股票的格式化输出要求
        for s in stocks:
            code = str(s.get("代码") or s.get("股票代码", "")).strip()
            name = s.get("名称", s.get("股票名称", "?"))
            prompt += f"\n## {name}（{code}）\n推荐买入价：XX元\n止损触发：XX元\n第一目标：XX元\n第二目标：XX元\n操作建议：买入/观望/回避"

        system = """你是专业的A股短线交易专家。
结合量价形态、技术支撑、大盘环境评估买入区。
止损触发：买入价下方5-8%，跌破代表逻辑失效需放弃。
目标：+10%（第一目标）、+20%（第二目标）。
输出格式严格如下（每只股票必须有独立的格式输出）：
## 股票名称（代码）
推荐买入价：XX元
止损触发：XX元
第一目标：XX元
第二目标：XX元
操作建议：买入/观望/回避"""

        # 共享 LLM 调用（P0-2），先检查 API Key 配置（P1-2）
        try:
            from config_loader import get_config
            cfg = get_config()
            api_key = cfg.get("llm", {}).get("api_key", "") or cfg.get("opencode", {}).get("api_key", "")
            if not api_key:
                print("[ReviewAgent] ⚠️ 未配置 LLM API Key，跳过重点观察池批量评估")
                return {}
        except Exception:
            print("[ReviewAgent] ⚠️ config_loader 加载失败，跳过重点观察池批量评估")
            return {}

        text = PoolManager._call_llm_for_limits(stocks_text, system, prompt, timeout=120, max_tokens=3000)
        if not text:
            return {}

        # ── 解析批量结果 ─────────────────────────────────
        result = {}
        # 按 ## 标题分割每只股票
        for s in stocks:
            code = str(s.get("代码") or s.get("股票代码", "")).strip()
            name = s.get("名称", s.get("股票名称", "?"))
            if not code:
                continue

            # 在LLM输出中找该股票的区域
            buy_zone, stop_trigger, target1, target2, advice = None, None, None, None, "观望"
            q = qmap.get(code, {})
            cur_price = q.get("现价", 0)

            for line in text.split("\n"):
                # 匹配当前股票
                if name in line and code in line:
                    continue  # 跳过标题行
                m = re.match(r"推荐买入价[：:]\s*([\d.]+)", line)
                if m and not buy_zone:
                    buy_zone = round(float(m.group(1)), 2)
                m = re.match(r"止损触发[：:]\s*([\d.]+)", line)
                if m and not stop_trigger:
                    stop_trigger = round(float(m.group(1)), 2)
                m = re.match(r"第一目标[：:]\s*([\d.]+)", line)
                if m and not target1:
                    target1 = round(float(m.group(1)), 2)
                m = re.match(r"第二目标[：:]\s*([\d.]+)", line)
                if m and not target2:
                    target2 = round(float(m.group(1)), 2)
                m = re.match(r"操作建议[：:]\s*(\S+)", line)
                if m:
                    advice = m.group(1)

            # 兜底计算
            if not buy_zone:
                buy_zone = round(cur_price * 1.01, 2) if cur_price else None
            if not stop_trigger:
                stop_trigger = round(buy_zone * 0.95, 2) if buy_zone else None
            if not target1:
                target1 = round(buy_zone * 1.10, 2) if buy_zone else None
            if not target2:
                target2 = round(buy_zone * 1.20, 2) if buy_zone else None

            result[code] = {
                "推荐买入价": buy_zone,
                "止损触发": stop_trigger,
                "第一目标": target1,
                "第二目标": target2,
                "操作建议": advice,
                "今日收盘": cur_price,
                "今日涨跌": f"{q.get('涨跌幅', 0):+.2f}%" if isinstance(q.get('涨跌幅'), float) else q.get("涨跌幅", "—"),
                "PE": q.get("市盈率_TTM", "—"),
                "换手率": q.get("换手率", 0),
                "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }

        return result

    @staticmethod
    def _fetch_quotes_for_stocks(stocks: list) -> dict:
        """统一拉取一批股票的行情，返回 {代码: 行情dict}（P1-1：一次拉取复用）"""
        import sys
        for mod in list(sys.modules.keys()):
            if 'market_agent' in mod:
                del sys.modules[mod]

        codes_raw = [
            str(s.get("代码") or s.get("股票代码", "")).strip()
            for s in stocks
            if (s.get("代码") or s.get("股票代码", ""))
        ]
        if not codes_raw:
            return {}

        api_codes = [to_api(c) for c in codes_raw]
        try:
            quotes = fetch_quotes(api_codes)
        except Exception:
            return {}
        return {item["代码"]: item for item in quotes}

    def _build_realtime_section(self, stocks: list, qmap: dict) -> str:
        """
        使用已拉取的行情数据构建实时行情表格。
        解决审查层 LLM 盲审问题。
        （P2-1修复：增加更多技术指标字段）
        """
        if not stocks:
            return ""

        lines = [
            "**【实时行情 - 审查前刷新】**",
            "| 代码 | 名称 | 现价 | 涨跌% | PE_TTM | 换手% | 量比 | 振幅% | 成交额(万) |",
            "|------|------|------|------|--------|------|------|------|-----------|",
        ]
        has_data = False
        for s in stocks:
            raw = str(s.get("代码") or s.get("股票代码", "")).strip()
            if not raw or raw == "000000":
                continue
            q = qmap.get(raw, {})
            name = s.get("名称") or s.get("股票名称", q.get("名称", "?"))
            if q:
                has_data = True
                price = q.get("现价", 0)
                chg = q.get("涨跌幅", 0)
                pe = q.get("市盈率_TTM", "—")
                turnover = q.get("换手率", 0)
                vol_ratio = q.get("量比", 0)
                # P2-1：新增振幅和成交额字段
                high = q.get("最高价", 0)
                low = q.get("最低价", 0)
                prev_close = q.get("昨收价", 0)
                amount = q.get("成交额_万", 0)
                
                # 计算振幅
                if prev_close and prev_close > 0:
                    amplitude = (high - low) / prev_close * 100
                else:
                    amplitude = 0
                
                lines.append(
                    f"| {raw} | {name} | **{price:.2f}** | {chg:+.2f} | "
                    f"{pe} | {turnover:.2f} | {vol_ratio:.2f} | {amplitude:.2f} | {amount:.0f} |"
                )
        return "\n".join(lines) if has_data else ""

    def _format_stocks_with_quote(self, stocks: list, qmap: dict) -> str:
        """格式化候选股票列表（使用已拉取的行情，P1-1优化）"""
        if not stocks:
            return "（候选池为空）"
        lines = []
        for s in stocks:
            code = s.get("股票代码", s.get("代码", "?"))
            name = s.get("股票名称", s.get("名称", "?"))
            driver = s.get("驱动级别", "")
            q = qmap.get(str(code), {})
            if q:
                price = q.get("现价", 0)
                chg = q.get("涨跌幅", 0)
                pe = q.get("市盈率_TTM", "—")
                turnover = q.get("换手率", 0)
                driver_str = f" [驱动:{driver}]" if driver else ""
                lines.append(
                    f"- {code} {name}{driver_str}  现价{price:.2f}({chg:+.2f}%) PE={pe} 换手{turnover:.2f}%"
                )
            else:
                driver_str = f" [驱动:{driver}]" if driver else ""
                lines.append(f"- {code} {name}{driver_str}")
        return "\n".join(lines)

    def _count_stocks(self, text: str) -> int:
        return len(re.findall(r"\d{6}", text))

    def _get_market_state(self) -> dict:
        """获取市场状态（用于评分通缩和prompt上下文）"""
        try:
            import json
            sm_file = self.root / "data" / "shared_memory.json"
            if sm_file.exists():
                data = json.loads(sm_file.read_text(encoding="utf-8"))
                if data and isinstance(data, list):
                    sh = next((s for s in data if s.get("代码") == "000001"), None)
                    if sh:
                        sh_chg = float(sh.get("涨跌幅", 0))
                        return {"state": "偏多" if sh_chg > 1 else "震荡偏强" if sh_chg > 0 else "震荡偏弱" if sh_chg > -1 else "偏空", "s_pool_cap": 2 if sh_chg > 0 else 1 if sh_chg > -1 else 0, "sh_chg": sh_chg}
        except Exception:
            pass
        return {"state": "震荡", "s_pool_cap": 2, "sh_chg": 0}


    # ─────────────────────────────────────────
    # 统一代码提取：基于 LLM 规范格式
    # （不再依赖6种regex兜底，LLM应输出规范格式）
    # ─────────────────────────────────────────
    @staticmethod
    def _extract_stock_from_block(block: str):
        """
        从单个股票块中提取 (代码, 名称)。
        基于 LLM 规范格式：## [代码] 股票名称
        简化正则，避免多种格式的脆弱兜底。
        """
        code, name = None, None
        # 优先：## [600118] 股票名称 或 ## 600118 股票名称
        for pat in [
            r'##\s*\[?(\d{6})\]?\s*([\u4e00-\u9fa5]{2,8})',
        ]:
            m = re.search(pat, block, re.MULTILINE)
            if m:
                code, name = m.group(1), m.group(2)
                break
        return code, name

    def _format_review_result(self, result: str) -> str:
        if "[LLM调用失败]" in result:
            return f"# 审查结果\n\n[LLM调用失败]\n\n{result}"
        return f"# 审查结果\n\n{result}"

    def _generate_pool_updates(self, result: str) -> str:
        """
        生成五池更新摘要（仅用于报告格式化，不写文件）。
        """
        lines = ["## 五池更新\n"]
        upgrades, demotions = [], []
        # 统一规范格式分割
        blocks = re.split(
            r'(?=##\s*\[?\d{6}\]?\s*[\u4e00-\u9fa5])',
            result
        )
        for block in blocks:
            code, name = self._extract_stock_from_block(block)
            if not code:
                continue
            if re.search(r'→\s*(→)?\s*升级', block):
                upgrades.append((name, code))
            elif re.search(r'→\s*(→)?\s*降级', block):
                demotions.append((name, code))
            # 保留 → 候选池 不参与池流转

        if upgrades:
            lines.append("### 升级→重点观察池")
            for name, code in upgrades[:5]:
                lines.append(f"- {name}（{code}）")
        if demotions:
            lines.append("### 降级→边缘池")
            for name, code in demotions[:5]:
                lines.append(f"- {name}（{code}）")
        if not upgrades and not demotions:
            lines.append("（本期无池流转）")
        return "\n".join(lines)

    def _parse_review_result(self, result: str) -> dict[str, dict]:
        """
        解析 LLM 审查报告，提取每只股票的完整元数据。
        返回: {代码: {代码, 名称, 综合分, 信心度, 驱动级别, 核心逻辑}}
        """
        stocks = {}

        def _score_to_level(score: int) -> str:
            if score >= 90: return "S级"
            if score >= 75: return "A级"
            if score >= 65: return "B级(黄色预警)"
            if score >= 55: return "C级(观察区)"
            return "D级(淘汰)"
        # 统一规范格式分割
        blocks = re.split(
            r'(?=##\s*\[?\d{6}\]?\s*[\u4e00-\u9fa5])',
            result
        )
        for block in blocks:
            code, name = self._extract_stock_from_block(block)
            if not code:
                continue

            # 综合评分（支持 LLM 自由格式：数字 + 描述）
            score = 0
            sm = re.search(r'综合评分[^\d]*?(\d+)', block)
            if sm:
                score = int(sm.group(1))
            # 信心度
            confidence = ""
            cm = re.search(r'信心度[：:]?\s*([^*|\n]+)', block)
            if cm:
                confidence = cm.group(1).strip()
            # 核心逻辑
            logic_parts = []
            for dim, pat in [
                ("驱动验证", r"驱动验证[^\d]*?\d+\s*[^\n|]*?\|\s*([^\n|]+)"),
                ("位置分析", r"位置分析[^\d]*?\d+\s*[^\n|]*?\|\s*([^\n|]+)"),
            ]:
                pm = re.search(pat, block)
                if pm and pm.group(1).strip():
                    logic_parts.append(pm.group(1).strip())
            core_logic = "；".join(logic_parts) if logic_parts else "四维审查综合判断"

            stocks[code] = {
                "代码": code,
                "名称": name,
                "综合分": score,
                "信心度": confidence,
                "驱动级别": _score_to_level(score),
                "核心逻辑": core_logic,
            }
        return stocks

    def _parse_review_result_v2(self, result: str) -> ReviewResult:
        """
        V2 解析：返回 ReviewResult 结构（使用 schemas dataclass）
        不再使用脆弱的多格式 regex 兜底。
        """
        from datetime import datetime as _dt

        def _score_to_level(score: int) -> str:
            if score >= 90: return "S级"
            if score >= 75: return "A级"
            if score >= 65: return "B级(黄色预警)"
            if score >= 55: return "C级(观察区)"
            return "D级(淘汰)"

        def _extract_dimensions(block: str) -> List[DimensionScore]:
            """提取四维评分"""
            dims = []
            dim_map = {
                "驱动验证": r'驱动验证[^\\d]*?(\\d+)',
                "位置分析": r'位置分析[^\\d]*?(\\d+)',
                "量能判断": r'量能判断[^\\d]*?(\\d+)',
                "风险扫描": r'风险扫描[^\\d]*?(\\d+)',
            }
            for dim_name, pat in dim_map.items():
                m = re.search(pat, block)
                if m:
                    s = int(m.group(1))
                    note_m = re.search(rf'{dim_name}[^\\n|]*\\|[^\\n|]*\\|\\s*([^\\n|]+)', block)
                    note = note_m.group(1).strip() if note_m else ""
                    dims.append(DimensionScore(dimension=dim_name, score=s, note=note))
            return dims

        def _extract_flow(block: str) -> tuple[str, str]:
            """提取流转方向和目标池"""
            if re.search(r'→\s*(→)?\s*升级', block):
                return "升级", "重点观察池"
            if re.search(r'→\s*(→)?\s*降级', block):
                return "降级", "边缘池"
            return "保留", ""

        stocks: List[StockReview] = []
        upgrades: List[StockReview] = []
        demotions: List[StockReview] = []

        # 统一规范格式分割
        blocks = re.split(
            r'(?=##\s*\[?\d{6}\]?\s*[\u4e00-\u9fa5])',
            result
        )
        for block in blocks:
            code, name = self._extract_stock_from_block(block)
            if not code:
                continue

            # 综合评分
            score = 0
            sm = re.search(r'综合评分[^\d]*?(\d+)', block)
            if sm:
                score = min(int(sm.group(1)), 100)

            # 信心度
            confidence = ""
            cm = re.search(r'信心度[：:]\s*([^*|\n]+)', block)
            if cm:
                confidence = cm.group(1).strip()

            # 流转方向
            flow_dir, target_pool = _extract_flow(block)

            # ═══ P0: 硬阈值 — 分数<75即使LLM说升级也拒绝 ═══
            if flow_dir == "升级" and score < 75:
                flow_dir = "保留"
                target_pool = ""
            # ═══════════════════════════════════════════════════

            # 四维评分
            dims = _extract_dimensions(block)

            # 核心逻辑
            logic_parts = []
            for dim in dims:
                if dim.note:
                    logic_parts.append(dim.note)
            core_logic = "；".join(logic_parts[:3]) if logic_parts else "四维审查综合判断"

            # 重点池额外字段（从池数据中获取）
            rec_buy = stop_loss = target_1 = target_2 = None
            cur_price = 0.0
            today_chg = ""
            advice = ""

            # 尝试从候选池/重点池中获取已有字段
            pool_file = self.pool_dir / "重点观察池.json"
            if pool_file.exists():
                data = self.safe_read_json(pool_file, {})
                for s in data.get("stocks", []):
                    sc = str(s.get("代码", s.get("股票代码", ""))).strip()
                    if sc == code:
                        rec_buy = s.get("推荐买入价")
                        stop_loss = s.get("止损触发")
                        target_1 = s.get("第一目标")
                        target_2 = s.get("第二目标")
                        cur_price = s.get("今日收盘", 0) or 0
                        today_chg = s.get("今日涨跌", "")
                        advice = s.get("操作建议", "")
                        break

            sr = StockReview(
                code=code,
                name=name,
                composite_score=score,
                confidence=confidence,
                driver_level=_score_to_level(score),
                dimensions=dims,
                core_logic=core_logic,
                flow_direction=flow_dir,
                target_pool=target_pool,
                entry_date=datetime.now().strftime("%Y-%m-%d"),
                recommended_buy=float(rec_buy) if rec_buy else None,
                stop_loss=float(stop_loss) if stop_loss else None,
                target_1=float(target_1) if target_1 else None,
                target_2=float(target_2) if target_2 else None,
                action_advice=advice,
                current_price=float(cur_price) if cur_price else None,
                today_change=today_chg,
            )

            # ── P1-2：过热检测（从候选池获取实时数据）──────────────
            overheat_info = self._check_overheat(code, sr)
            if overheat_info:
                sr.core_logic += f" | ⚠️ 过热检测: {overheat_info['reason']}"
                if overheat_info["overheat_level"] == "critical":
                    # critical：强制降级
                    sr.flow_direction = "降级"
                    sr.target_pool = "边缘池"
                    sr.action_advice = "回避"
                    sr.composite_score = max(0, sr.composite_score - overheat_info["penalty"])
                    sr.driver_level = _score_to_level(sr.composite_score)
                    print(f"[ReviewAgent] 🔥 过热检测 CRITICAL: {name}({code}) - {overheat_info['reason']}")
                elif overheat_info["overheat_level"] == "warning":
                    original_score = sr.composite_score
                    sr.composite_score = max(0, original_score - overheat_info["penalty"])
                    sr.driver_level = _score_to_level(sr.composite_score)
                    # 过热WARNING降级：原评分70-74区间触发过热→自动降级
                    # 解决RULE5.5(涨幅>8%+评分≥70)扣5分后70-73分不触发降级的漏检盲区
                    if sr.composite_score <= 65 or (70 <= original_score < 75):
                        sr.flow_direction = "降级"
                        sr.target_pool = "边缘池"
                        if not sr.action_advice:
                            sr.action_advice = "过热降级"
                        sr.core_logic += f" | ⚠️ 过热WARNING降级: 原分{original_score}扣{sr.composite_score}>降级区"
                        print(f"[ReviewAgent] ⚠️ 过热WARNING降级: {name}({code}) 原{original_score}→{sr.composite_score}分 → 边缘池")
                    print(f"[ReviewAgent] ⚠️ 过热检测 WARNING: {name}({code}) - {overheat_info['reason']}")

            # ── P1-2：一票否决强制降级（解决沪硅产业降级延迟问题）──────
            # 对有亏损/一票否决风险但评分≥60的标的强制降级
            # 防止LLM被表面逻辑迷惑而给高分
            risk_indicators = []
            if sr.dimensions:
                for dim in sr.dimensions:
                    if dim.dimension == "风险扫描" and dim.note:
                        risk_note = dim.note.lower()
                        if any(kw in risk_note for kw in ["亏损", "pe<0", "pe为负", "连续亏损", "退市", "st", "商誉过高", "解禁"]):
                            risk_indicators.append(dim.note)
            
            if risk_indicators and sr.composite_score >= SCORE_C_LEVEL:
                # 有风险信号但分数≥55，强制降级
                sr.flow_direction = "降级"
                sr.target_pool = "边缘池"
                sr.action_advice = "一票否决"
                risk_text = "；".join(risk_indicators[:2])
                sr.core_logic += f" | 🚫 一票否决强制降级: {risk_text}"
                # 评分降至55以下，确保不会误升级
                sr.composite_score = max(0, min(sr.composite_score, 54))
                sr.driver_level = _score_to_level(sr.composite_score)
                print(f"[ReviewAgent] 🚫 一票否决强制降级: {name}({code}) 风险信号={risk_text} → 边缘池")
            
            # ── P0-降级延迟修复：硬性降级阈值（<AUTO_DOWNGRADE_SCORE分强制降级）─────────
            # 解决LLM提示词降级区间55-64与代码盲区对齐问题
            if sr.composite_score < AUTO_DOWNGRADE_SCORE:
                sr.flow_direction = "降级"
                sr.target_pool = "边缘池"
                if sr.action_advice == "":
                    sr.action_advice = "低分淘汰"
                sr.core_logic += f" | 🔴 硬性降级：{sr.composite_score}分<{AUTO_DOWNGRADE_SCORE}分阈值"
                print(f"[ReviewAgent] 🔴 硬性降级: {name}({code}) {sr.composite_score}分<{AUTO_DOWNGRADE_SCORE}分 → 边缘池")

            # ── 黄色预警标记：60-74分且未降级的标的标记观察 ──
            # 25天悬空修复：代码级强制黄色预警，不依赖LLM诚实度
            if sr.flow_direction != "降级" and YELLOW_ALERT_MIN <= sr.composite_score < DECISION_MIN_SCORE:
                adv = f"黄色预警({sr.composite_score}分)"
                if sr.action_advice:
                    sr.action_advice += " | " + adv
                else:
                    sr.action_advice = adv
                sr.core_logic += f" | 🟡 {adv}"
                if sr.flow_direction == "":
                    sr.flow_direction = "保留"
                if not sr.target_pool:
                    sr.target_pool = "候选池"

            stocks.append(sr)
            if sr.flow_direction == "升级":
                upgrades.append(sr)
            elif sr.flow_direction == "降级":
                demotions.append(sr)
            
            # ── P2-3：T+1追踪记录 ──────────────────────────────
            from closed_loop_tracker import ClosedLoopTracker
            tracker = ClosedLoopTracker()
            tracker.record_review(
                code=code,
                name=name,
                score=sr.composite_score,
                level=sr.driver_level,
                flow_direction=sr.flow_direction,
                target_pool=sr.target_pool,
                action_advice=sr.action_advice or "",
            )

        review_output = ReviewOutput(
            raw_text=result,
            timestamp=datetime.now().isoformat(),
        )
        return ReviewResult(
            success=True,
            output=review_output,
            stocks=stocks,
            upgrades=upgrades,
            demotions=demotions,
        )

    # ── P1-2：过热检测 ──────────────────────────────────────
    def _check_overheat(self, code: str, stock_review: StockReview) -> Optional[dict]:
        """
        过热检测：检查股票是否过热（涨幅过大+高估值+高换手+高量比）

        规则（P0修复：放宽阈值，减少误杀）：
        - CRITICAL：涨幅>12% + (PE>80 或 换手>12%) → 强制降级
        - WARNING-1：涨幅>8% + 评分>70 → 扣10分
        - WARNING-2：涨幅>10% → 扣5分
        - WARNING-3：涨幅>5% + 量比>3 → 扣5分（高位放量）

        Args:
            code: 股票代码
            stock_review: 当前审查结果

        Returns:
            {"overheat_level": "critical"|"warning", "penalty": int, "reason": str} 或 None
        """
        # 从候选池获取实时数据
        candidate_pool = self.pool_manager.load_pool("快筛候选池")
        key_watch_pool = self.pool_manager.load_pool("重点观察池")

        stock_data = None
        for s in candidate_pool.get("stocks", []) + key_watch_pool.get("stocks", []):
            s_code = str(s.get("代码", s.get("股票代码", ""))).strip()
            if s_code == code:
                stock_data = s
                break

        if not stock_data:
            return None

        # 获取实时行情
        api_code = to_api(code)
        try:
            quotes = fetch_quotes([api_code])
        except Exception:
            quotes = []

        quote = next((q for q in quotes if q.get("代码") == code), {}) if quotes else {}

        # 提取指标
        change_pct = 0.0
        pe_ttm = 0.0
        turnover = 0.0
        volume_ratio = 1.0
        month_chg = 0.0  # 月涨跌
        quarter_chg = 0.0  # 季涨跌
        amplitude = 0.0  # 振幅

        if quote:
            change_pct = float(quote.get("涨跌幅", 0) or 0)
            pe_ttm = float(quote.get("市盈率_TTM", 0) or 0)
            turnover = float(quote.get("换手率", 0) or 0)
            volume_ratio = float(quote.get("量比", 1) or 1)
            month_chg = float(quote.get("月涨跌", 0) or 0)
            quarter_chg = float(quote.get("季涨跌", 0) or 0)
            amplitude = float(quote.get("振幅", 0) or 0)
        else:
            # 从池数据中获取
            change_str = stock_data.get("今日涨跌", "0%")
            try:
                change_pct = float(change_str.replace("%", "").replace("+", ""))
            except:
                change_pct = 0.0
            pe_ttm = float(stock_data.get("PE", 0) or 0)
            turnover = float(stock_data.get("换手率", 0) or 0)
            volume_ratio = float(stock_data.get("量比", 1) or 1)

        # 委托 OverheatDetector 执行纯规则检测
        mkt_state = self._get_market_state().get("state", "震荡")
        return OverheatDetector.detect(
            change_pct=change_pct,
            pe_ttm=pe_ttm,
            turnover=turnover,
            volume_ratio=volume_ratio,
            month_chg=month_chg,
            quarter_chg=quarter_chg,
            composite_score=stock_review.composite_score,
            amplitude=amplitude,
            market_state=mkt_state,
        )

    def _find_pool_of_stock(self, code: str) -> Optional[str]:
        """查找股票当前所在的池（排除持仓池），返回池名称或None"""
        pool_map = {
            "快筛候选池": self.pool_manager.load_pool("快筛候选池").get("stocks", []),
            "重点观察池": self.pool_manager.load_pool("重点观察池").get("stocks", []),
            # 接近决策池已停用（盟主确认删除）
            "边缘池": self.pool_manager.load_pool("边缘池").get("stocks", []),
        }
        for pool_name, stocks in pool_map.items():
            if any(s.get("代码") == code for s in stocks):
                return pool_name
        return None

    def _remove_from_pool(self, pool_name: str, codes: list[str]):
        """从指定池中移除股票"""
        if not codes:
            return
        pool = self.pool_manager.load_pool(pool_name)
        stocks = pool.get("stocks", [])
        original_count = len(stocks)
        new_stocks = [s for s in stocks if s.get("代码") not in codes]
        if len(new_stocks) < original_count:
            pool["stocks"] = new_stocks
            # P0-1: 同步更新持仓数统计
            pool["统计"] = pool.get("统计", {})
            pool["统计"]["持仓数"] = len(new_stocks)
            pool["统计"]["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.pool_manager.save_pool(pool_name, pool)
            print("ReviewAgent:", f"remove_from_pool", f"{pool_name}: 移除 {codes}")

    def _apply_pool_updates(self, result: str, upgrades_sr: list = None, demotions_sr: list = None):
        """
        应用池流转更新：
        1. 使用后处理后的 upgrades/demotions 列表（包含过热/一票否决/硬性降级处理后的流向）
        2. 升级时从候选池移除目标股
        3. 降级时从候选/重点池移除目标股
        4. 池间去重：加入新池前检查该股是否已在其他池
        """
        parsed = self._parse_review_result(result)

        # 新增：对核心逻辑为空/占位符的，用v2解析的维度备注补充
        v2_logics = {}
        try:
            parsed_v2 = self._parse_review_result_v2(result)
            for sr in parsed_v2.stocks:
                notes = [d.note for d in sr.dimensions if d.note]
                if notes:
                    v2_logics[sr.code] = "；".join(notes[:3])
        except Exception:
            v2_logics = {}

        upgrades, demotions = [], []
        
        # 优先使用后处理后的列表（包含过热/一票否决/硬性降级的修正）
        if upgrades_sr or demotions_sr:
            upgrades = [(sr.name, sr.code) for sr in (upgrades_sr or [])]
            demotions = [(sr.name, sr.code) for sr in (demotions_sr or [])]
        else:
            # 降级：从LLM原始文本提取（旧路径，不应触发）
            blocks = re.split(
                r'(?=##\s*\[?\d{6}\]?\s*[\u4e00-\u9fa5])',
                result
            )
            for block in blocks:
                code, name = self._extract_stock_from_block(block)
                if not code:
                    continue
                if re.search(r'→\s*(→)?\s*升级', block):
                    upgrades.append((name, code))
                elif re.search(r'→\s*(→)?\s*降级', block):
                    demotions.append((name, code))
            # 保留 → 候选池 不参与池流转（已在候选池中）

        # ── Step 2：升入重点观察池 ──
        if upgrades:
            upgrade_stocks = []
            for name, code in upgrades:
                meta = parsed.get(code, {})
                print("ReviewAgent:", f"ReviewAgent", f"✅ {name}({code}) 审查完成：{meta.get('综合分','?')}分")
                # 先从候选池移除（无论在哪）
                self._remove_from_pool("快筛候选池", [code])
                upgrade_stocks.append({
                    "代码": code,
                    "名称": name,
                    "综合分": meta.get("综合分", 0),
                    "信心度": meta.get("信心度", ""),
                    "驱动级别": meta.get("驱动级别", ""),
                    "核心逻辑": meta.get("核心逻辑", "") if meta.get("核心逻辑", "") not in ("", "四维审查综合判断") else v2_logics.get(code, meta.get("核心逻辑", "")),
                    "纳入日期": datetime.now().strftime("%Y-%m-%d"),
                })
            self._add_to_pool("重点观察池", upgrade_stocks, skip_pools=["快筛候选池"])

        # ── Step 3：降级到边缘池 ──
        if demotions:
            demote_stocks = [
                {"代码": code, "名称": name, "降级时间": datetime.now().strftime("%Y-%m-%d")}
                for name, code in demotions
            ]
            self._remove_from_pool("快筛候选池", [c for _, c in demotions])
            self._remove_from_pool("重点观察池", [c for _, c in demotions])
            self._add_to_pool("边缘池", demote_stocks, skip_pools=["快筛候选池", "重点观察池"])

    def _add_to_pool(self, pool_name: str, new_stocks: list, skip_pools: list = None):
        """
        将股票加入目标池。跨池去重逻辑：
        - 若某股已在本池，跳过
        - 若某股已在 skip_pools 中的任一池，跳过（已在原池处理移除，此处仅做安全兜底）
        - 若某股在其他非skip池（说明流转路径异常），打印警告但不写入
        """
        pool_file = self.pool_dir / f"{pool_name}.json"
        if pool_file.exists():
            data = self.safe_read_json(pool_file, {})
        else:
            data = {"池名称": pool_name, "stocks": [], "历史记录": [], "统计": {"创建日期": datetime.now().strftime("%Y-%m-%d"), "累计进入": 0}}

        # 读取时兼容新旧格式
        existing = data.get("stocks", [])
        existing_codes = {str(s.get("代码") or s.get("股票代码", "")) for s in existing}
        skip_set = set(skip_pools or [])

        # ALL_POOLS 排除持仓池（持仓池不参与流转）和目标池
        # P0-2026-06-04: 加入S级操作池防跨池重复（之前漏了这只有3/3与重点池重叠）
        all_trade_pools = ["快筛候选池", "重点观察池", "边缘池", "S级操作池"]
        other_pools = [p for p in all_trade_pools if p != pool_name and p not in skip_set]

        filtered = []
        for s in new_stocks:
            code = str(s.get("代码") or s.get("股票代码", ""))
            # ① 本池已有
            if code in existing_codes:
                self.logger.info("skip_add_already_in_pool",
                               code=code, pool=pool_name, reason="already_exists")
                continue
            # ② skip_pools 里已有（应已被移除，此处安全兜底）
            if skip_set:
                in_skip = any(
                    code in {
                        str(x.get("代码", "")) for x in
                        self.safe_read_json(self.pool_dir / f"{p}.json", {}).get("stocks", [])
                    }
                    for p in skip_set
                )
                if in_skip:
                    self.logger.info("skip_add_in_skip_pool",
                                   code=code, pool=pool_name, skip=list(skip_set))
                    continue
            # ③ 在其他非skip池（异常：流转路径不合法）
            in_other = False
            for other_pool in other_pools:
                other_data = self.safe_read_json(self.pool_dir / f"{other_pool}.json", {})
                other_codes = {str(x.get("代码", "")) for x in other_data.get("stocks", [])}
                if code in other_codes:
                    self.logger.warning("skip_add_cross_pool",
                                      code=code, target=pool_name,
                                      found_in=other_pool,
                                      reason="already_in_other_pool")
                    in_other = True
                    break
            if in_other:
                continue
            filtered.append(s)

        # ── 重点观察池：LLM 批量评估（P1-2：一次调用搞定所有）────────
        if pool_name == "重点观察池" and filtered:
            batch_result = self._batch_enrich_key_watch(filtered)
            for s in filtered:
                code = str(s.get("代码") or s.get("股票代码", ""))
                name = s.get("名称", s.get("股票名称", "?"))
                if code in batch_result:
                    s.update(batch_result[code])
                    r = batch_result[code]
                    print(f"[ReviewAgent] ✅ {name}({code}) 重点观察池评估："
                          f"买入:{r.get('推荐买入价')} 止损:{r.get('止损触发')} "
                          f"目标:{r.get('第一目标')}/{r.get('第二目标')} → {r.get('操作建议')}")

        # ── 溢出处理：超容量时最旧的移入对应历史池 ────────────
        capacity = 20  # 默认容量
        if pool_name == "边缘池":
            # 边缘池是弃置区，超容时丢弃而不是归档
            all_stocks = existing + filtered
            if len(all_stocks) > capacity:
                keep = all_stocks[-capacity:]  # 保留最新的
                print(f"[ReviewAgent] ⚠️ {pool_name} 超{capacity}只，溢出{len(all_stocks)-capacity}只丢弃")
            else:
                keep = all_stocks
        else:
            all_stocks = existing + filtered
            if len(all_stocks) > capacity:
                overflow = all_stocks[:-capacity]  # 最旧的移出
                keep = all_stocks[-capacity:]      # 最新的保留
                self._archive_to_history(pool_name, overflow)
                print(f"[ReviewAgent] ⚠️ {pool_name} 超{capacity}只，{len(overflow)}只移入历史池")
            else:
                keep = all_stocks

        # 统一写到 stocks
        data["stocks"] = keep

        # 写历史记录
        data.setdefault("历史记录", [])
        today = datetime.now().strftime("%Y-%m-%d")
        # 合并当天记录
        existing_dates = {r.get("日期") for r in data["历史记录"]}
        for s in filtered:
            if today not in existing_dates:
                data["历史记录"].append({"日期": today, "进入": len(filtered)})
                existing_dates.add(today)

        # 写统计
        stats = data.get("统计", {})
        stats["累计进入"] = stats.get("累计进入", 0) + len(filtered)
        data["统计"] = stats

        self.safe_write_json(pool_file, data)
        self.logger.pool_operation(pool_name, "upgrade" if "重点" in pool_name else "demote", count=len(filtered))

    # ── 历史归档池 ───────────────────────────────────────
    def _archive_to_history(self, source_pool: str, overflow_stocks: list):
        """
        将超量股票归档到历史池。
        历史池：{source_pool}_历史池.json（与原池同目录）
        """
        import json as _json

        history_file = self.pool_dir / f"{source_pool}_历史池.json"
        if history_file.exists():
            history_data = self.safe_read_json(history_file, {"池名称": f"{source_pool}历史池", "stocks": [], "历史记录": []})
        else:
            history_data = {
                "池名称": f"{source_pool}历史池",
                "stocks": [],
                "历史记录": [],
                "统计": {"创建日期": datetime.now().strftime("%Y-%m-%d")},
            }

        # 去重
        existing_codes = {str(s.get("代码", "")) for s in history_data.get("stocks", [])}
        new_archive = [s for s in overflow_stocks if str(s.get("代码", "")) not in existing_codes]

        if new_archive:
            history_data.setdefault("stocks", []).extend(new_archive)
            history_data["stocks"] = history_data["stocks"][-200:]  # 最多保留200只历史
            history_data.setdefault("历史记录", []).append({
                "日期": datetime.now().strftime("%Y-%m-%d"),
                "移出池": source_pool,
                "数量": len(new_archive),
            })
            self.safe_write_json(history_file, history_data)
            for s in new_archive:
                name = s.get("名称", s.get("股票名称", "?"))
                code = s.get("代码", s.get("股票代码", "?"))
                print(f"[ReviewAgent] 📦 {name}({code}) → {source_pool}历史池")


if __name__ == "__main__":
    agent = ReviewAgent()
    result = agent.run()
    if result["success"]:
        print(f"✅ 审查完成")
        print(f"📄 保存: {result['saved_to']}")
        print("\n" + result["report"][:800])