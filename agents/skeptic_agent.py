#!/usr/bin/env python3
"""
Skeptic Agent - 怀疑者 Agent（冷静质疑版）
五维质疑：驱动逻辑/位置分析/量能判断/风险低估/方案矛盾
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from base_agent import BaseAgent, build_agent_system_prompt
from logger import StructuredLogger

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))


SYSTEM_PROMPT = """你是一个冷静的股票质疑专家，负责对每只候选股票进行严格的五维质疑审查。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【质疑者宣言】
"我不相信任何未经证实的逻辑。每一个推荐理由，都必须经得起推敲。"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 五维质疑框架（深度版）

### 维度1：驱动逻辑质疑 — 利好是真实、可量化、边际递增的吗？
核心质疑方向：
- **真实性**：利好是否有官方文件/财报/公告佐证？不是小道消息或模糊概念？
- **业绩质量**：若涉及业绩改善，是扣非主营业务增长，还是靠卖资产/补贴等非经常性损益？营收增速与利润增速是否匹配？经营现金流是否跟上利润增长？
- **利好边际**：这是首次出现的增量信息，还是已被市场反复炒作的"旧闻新炒"？利好能带来多少可量化的业绩增量（如政策落地后对应订单/营收的提升幅度），而非纯概念性炒作？
- **逻辑传导**："利好→业绩增长"的传导链是否成立？公司是直接受益标的，还是板块跟风边缘股？技术突破能否真正转化为产品和收入？
⚠️ 质疑点：数据来源、时效性、可证伪性、业绩质量

### 维度2：位置分析质疑 — 当前位置有安全边际吗？
核心质疑方向：
- **历史位置**：相比历史高点调整幅度够吗？处于52周什么分位？是"高位横盘"还是"低位启动"？
- **估值分位**：当前PE/PB处于自身历史什么分位（近3年/近5年）？和同行业可比公司相比估值是偏高还是偏低？避免把"估值高位的下跌中继"当成"低位启动"。
- **趋势性质**：当前是下跌趋势中的反弹（仍在下降通道内），还是反转趋势的启动（有效突破趋势线/站上年线）？"低位"不等于"安全"——下跌趋势里的低位往往还有更低。
- **周期适配**：如果是强周期股（资源/化工/航运等），当前处于行业周期什么阶段？周期顶部的"低估值"往往是陷阱，周期底部的"高估值"反而可能是机会。
- **预期透支**：当前股价是否已透支了近期所有利好预期？
⚠️ 质疑点：相对位置、估值合理性、趋势性质、周期阶段

### 维度3：量能判断质疑 — 量价配合健康吗？
核心质疑方向：
- **量价匹配**：放量对应上涨还是下跌？低位放量上涨=资金介入，高位放量滞涨/放量下跌=出货。上涨中缩量回调=良性筹码锁定，下跌中缩量=无人接盘。
- **换手率异常**：当前换手率是否超过对应市值的健康阈值？（提示：LLM输入中包含当前换手率vs阈值数据，直接判断即可）
- **持续性**：放量是单日脉冲还是连续多日？持续放量才代表资金真正进场。
- **资金性质**：放量背后是机构中长期进场，还是游资短期炒作？机构主导的行情持续性更强，游资主导的波动大、离场快。
⚠️ 质疑点：量能持续性、换手率异常、量价匹配

### 维度4：风险低估质疑 — 隐性风险被充分识别了吗？
核心质疑方向：
- **财务质量风险**：大股东质押比例是否>50%？应收账款/存货占比是否异常偏高？是否存在"大存大贷"（货币资金多+有息负债也高）的财务可疑特征？
- **行业系统性风险**：面临行业性监管变化（集采/反垄断/环保限产）？行业整体景气度下行？个股很难对抗行业趋势。
- **流动性风险**：日均成交额是否<5000万？流动性差的股票买卖滑点大，可能出现无量下跌，想止损都卖不出去。
- **未决风险**：重大未决诉讼/仲裁？被交易所下发问询函/监管警示函？这些往往是财务暴雷的前兆。
- **解禁与减持**：有无大规模解禁临近？大股东/高管有无减持计划？
⚠️ 质疑点：潜在风险、被忽视的隐性风险

### 维度5：方案矛盾质疑 — 买入方案逻辑自洽吗？
核心质疑方向：
- **买卖逻辑对应**：买入的核心逻辑是什么？对应的卖出条件是否匹配？做到"逻辑买入，逻辑卖出"——逻辑破了就要走，不因价格止损死扛。
- **止损合理性**：止损位基于技术面关键支撑（均线/平台低点），还是单纯比例？是否"太近易被洗出，太远亏损过大"？区分"逻辑止损"（基本面变坏）和"价格止损"（技术破位）。
- **仓位适配**：仓位是否与确定性/风险等级匹配？高确定性可高仓位，高波动题材只能小仓位试错。有无加减仓规则，而非一次性满仓？
- **风报比**：潜在盈利空间 vs 潜在亏损空间是否至少 2:1？
⚠️ 质疑点：逻辑自洽性、风控完备性、仓位合理性

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 风险分级标准（severity 取值）

| 级别 | 含义 | 触发条件 | 处理方式 |
|------|------|---------|---------|
| veto | 一票否决 | 财务造假嫌疑/重大监管处罚/基本面逻辑彻底证伪/高位巨额解禁+实控人减持 | 直接阻断买入 |
| high | 高风险 | 估值显著高于行业/短期换手率异常/核心逻辑存疑 | 必须解决才能执行 |
| medium | 中等风险 | 量能小幅异常/非核心风险/估值偏高但不离谱 | 建议关注但不强制 |
| low | 低风险 | 单日放量波动/短期消息面扰动/非核心业务小风险 | 可忽略 |

⚠️ **veto 必须附带 `veto_reason` 字段**，说明具体触发了哪条一票否决标准。

### 市场状态适配规则

根据SYSTEM PROMPT上下文中的"市场状态"字段自动调整质疑严厉度：

- **偏空/震荡偏弱（弱市）**：严格执行五维质疑。位置分析-趋势性质、风险低估全部维度默认 severity 提一级（medium→high, low→medium）。任何疑点都不应放过。
- **震荡偏强/偏多（强市）**：趋势延续性优先。
  ① 放宽"位置分析-估值分位"的质疑标准（high→medium）。
  ② 当存在明确的S级催化剂（政策文件/行业数据/公告订单/核心产品验证/龙头公司重大突破），且股价尚未完全反应时，"驱动逻辑"的质疑标准可考虑适度放宽（high→medium）。放宽时必须注明具体的催化剂来源和公告日期。
  ③ **催化剂强度平衡（P1-1收紧）**：S级催化剂仅允许放松驱动逻辑维度的质疑标准。其他4个维度的medium风险独立计数，medium≥2个时overall_verdict仍为challenge_required。summary必须注明未解决的风险和催化剂来源。
- **震荡（中性）**：中性质疑，按实际情况判断。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【输出格式】（必须JSON，禁止开场白）

```json
{
  "code": "000001",
  "name": "股票名称",
  "challenges": [
    {"dimension": "驱动逻辑", "question": "质疑内容", "severity": "high"},
    {"dimension": "位置分析", "question": "质疑内容", "severity": "high"},
    {"dimension": "量能判断", "question": "质疑内容", "severity": "medium"},
    {"dimension": "风险低估", "question": "质疑内容", "severity": "veto", "veto_reason": "财务造假嫌疑"},
    {"dimension": "方案矛盾", "question": "质疑内容", "severity": "low"}
  ],
  "overall_verdict": "pass / challenge_required",
  "summary": "质疑摘要（≤50字）"
}
```

⚠️ 硬性要求：
1. 每只股票必须回答全部5个维度
2. severity: veto > high > medium > low
3. severity=veto时，必须附带veto_reason字段说明触发标准
4. 弱市下位置分析-趋势性质、风险低估默认提一级
5. overall_verdict: pass=通过, challenge_required=需解决
6. 每只股票分析不超过100字

### 系统自动风险标记（优先级最高）- P0-1
自动标记是基于客观数据（PE/报告关键词等）直接生成的，优先级高于LLM对风险的判断：
- severity=veto 的自动标记 → LLM**不得**降级，保持veto
- severity=high 的自动标记 → LLM可审核确认，但若报告中提及的客观数据属实则保留
- 自动标记的question字段以【自动】开头，LLM不得移除
"""

USER_PROMPT_TEMPLATE = """请对以下重点观察池股票进行五维质疑审查：

重点观察池股票：
{candidate_stocks}

市场宏观背景：
{market_context}

市场状态：{market_state}

实时行情（含换手率阈值对比）：
{realtime_section}

参考审查报告：
{review_summary}

请为每只股票输出五维质疑报告（JSON格式）。"""


class SkepticAgent(BaseAgent):
    """怀疑者 Agent"""

    def __init__(self, agent_name: str = "SkepticAgent"):
        super().__init__(agent_name)
        self.logger = StructuredLogger("SkepticAgent")
        self.history_dir = self.root / "data" / "历史记录"

    def run(self, stock_list: List[Dict], review_report: str = "",
            market_context: Dict = None) -> Dict[str, Any]:
        """执行质疑审查"""
        with self.logger.agent_action("run"):
            return self.challenge(stock_list, review_report, market_context or {})

    def challenge(self, stock_list: list, review_report: str,
                   market_context: dict) -> dict:
        """
        对候选股票进行五维质疑审查

        Returns: {
            "success": bool,
            "challenges": [每只股票的质疑结果],
            "high_risk_stocks": [high级别质疑的股票],
            "report": str,
            "saved_to": str
        }
        """
        # 格式化输入
        candidate_stocks = self._format_stocks(stock_list)
        market_text = self._format_context(market_context)
        review_summary = self._extract_review(review_report)
        realtime_section = self._fetch_realtime(stock_list)
        market_state = self._get_market_state_from_index()

        # 构建提示词
        user_prompt = USER_PROMPT_TEMPLATE.format(
            candidate_stocks=candidate_stocks,
            market_context=market_text,
            market_state=market_state,
            realtime_section=realtime_section,
            review_summary=review_summary[:500] if review_summary else "（无）"
        )

        self.logger.llm_call("challenge_stocks", tokens=len(user_prompt))

        # LLM 调用
        result = self.call_llm(
            user_prompt,
            system=build_agent_system_prompt(SYSTEM_PROMPT, "SkepticAgent"),
            max_tokens=3000,
            temperature=0.3,
            response_format={"type": "json_object"}
        )

        # 解析与生成
        challenges = self._parse_challenges(result)
        # ── P0-1: 客观财务因子自动裁决覆盖 ──
        auto_flags = self._apply_auto_risk_overrides(stock_list, review_report, market_state)
        for flag in auto_flags:
            code = flag["code"]
            found = False
            for s in challenges:
                if s.get("code") == code:
                    s["challenges"].append({
                        "dimension": "风险低估",
                        "question": flag["reason"],
                        "severity": flag["severity"],
                        "veto_reason": flag.get("veto_reason")
                    })
                    found = True
                    break
            if not found:
                # LLM遗漏时兜底（跨代码匹配）
                for s in challenges:
                    if s.get("name", "") == flag.get("name", ""):
                        s["challenges"].append({
                            "dimension": "风险低估",
                            "question": flag["reason"],
                            "severity": flag["severity"],
                            "veto_reason": flag.get("veto_reason")
                        })
                        found = True
                        break
            if not found:
                challenges.append({
                    "code": code,
                    "name": flag.get("name", code),
                    "challenges": [{
                        "dimension": "风险低估",
                        "question": flag["reason"],
                        "severity": flag["severity"],
                        "veto_reason": flag.get("veto_reason")
                    }],
                    "overall_verdict": "challenge_required",
                    "summary": f"自动标记：{flag['reason'][:50]}"
                })
        high_risk = self._extract_high_risk(challenges)
        report = self._generate_report(challenges, high_risk, result)
        saved_to = self._save_report(report)
        self._save_verdict(challenges, high_risk)

        return {
            "success": True,
            "challenges": challenges,
            "high_risk_stocks": high_risk,
            "report": report,
            "saved_to": str(saved_to),
            "total_stocks": len(stock_list) if stock_list else 0,
            "high_risk_count": len(high_risk),
        }

    def _format_stocks(self, stock_list: list) -> str:
        if not stock_list:
            return "（无候选股票）"
        return "\n".join([
            f"- {s.get('代码', s.get('code', '?'))} {s.get('名称', s.get('name', '?'))}"
            for s in stock_list
        ])

    def _format_context(self, ctx: dict) -> str:
        if not ctx:
            return "（暂无市场上下文）"
        return "\n".join([f"- {k}: {v}" for k, v in ctx.items()])

    def _extract_review(self, report: str) -> str:
        if not report:
            return ""
        # 策略1：找"升级"相关关键词的上下文（老逻辑）
        lines = report.split("\n")
        for i, line in enumerate(lines):
            if "流转方向" in line or "升级" in line:
                return "\n".join(lines[max(0, i-2):i+15])
        # 策略2（v5.91）：找重点观察池评估表格（含全部17只股票评分）
        import re
        table_m = re.search(r"## 📋 重点观察池最新评估\n.*?(?=\n## |\Z)", report, re.DOTALL)
        if table_m:
            return table_m.group(0)
        # 兜底：前500字符
        return report[:500]

    def _fetch_realtime(self, stock_list: list) -> str:
        if not stock_list:
            return "（无股票）"
        try:
            for mod in list(sys.modules.keys()):
                if 'market_agent' in mod:
                    del sys.modules[mod]
            from market_agent import fetch_quotes, to_api

            codes = [s.get("代码", s.get("code", "")) for s in stock_list if s.get("代码", s.get("code"))]
            if not codes:
                return "（无有效代码）"

            quotes = fetch_quotes([to_api(c) for c in codes])
            qmap = {item["代码"]: item for item in quotes}

            lines = ["| 代码 | 名称 | 现价 | 涨跌 | 换手率 | 量比 | 市值 | 行业 | 换手阈值 |", "|------|------|------|------|--------|------|------|------|--------|"]
            for s in stock_list:
                code = s.get("股票代码", s.get("代码", s.get("code", "")))
                name = s.get("股票名称", s.get("名称", s.get("name", "?")))
                industry = s.get("行业", s.get("industry", ""))
                q = qmap.get(code, {})
                price = q.get("现价", "—")
                chg = q.get("涨跌幅", 0)
                turnover = q.get("换手率", 0)
                vol_ratio = q.get("量比", 0)
                market_cap = q.get("流通市值", 0) or q.get("总市值", 0)
                threshold = self.get_turnover_threshold(market_cap, industry) if isinstance(market_cap, (int, float)) and market_cap > 0 else "—"
                threshold_str = f"{threshold*100:.1f}%" if isinstance(threshold, float) else "—"
                market_cap_str = f"{market_cap:.1f}亿" if isinstance(market_cap, (int, float)) and market_cap > 0 else "—"
                chg_str = f"{chg:+.2f}%" if isinstance(chg, float) else str(chg)
                industry_str = industry if industry else "—"
                lines.append(f"| {code} | {name} | {price} | {chg_str} | {turnover:.2f}% | {vol_ratio:.2f} | {market_cap_str} | {industry_str} | {threshold_str} |")
            return "\n".join(lines)
        except Exception:
            return "（行情获取失败）"

    @staticmethod
    def get_turnover_threshold(market_cap: float, industry: str = "") -> float:
        """根据市值（亿元）和行业返回换手率健康阈值（P1-3行业修正）。
        
        基准阈值（按市值）：
        千亿以上 5% / 300-1000亿 8% / 100-300亿 12% / 100亿以下 18%
        
        行业修正系数：
        - 银行/保险/公用事业: 0.4x（流动性天然低）
        - 券商/多元金融: 1.2x（行情驱动，换手偏高）
        - 科技/半导体: 1.3x（高关注）
        - AI/人工智能: 1.4x
        - 传媒/游戏/影视: 1.5x（题材驱动）
        - 其他: 1.0x（标准）
        """
        if market_cap >= 1000:
            base = 0.05
        elif market_cap >= 300:
            base = 0.08
        elif market_cap >= 100:
            base = 0.12
        else:
            base = 0.18
        
        # 行业修正
        industry_multiplier = {
            "银行": 0.4, "保险": 0.4, "电力": 0.5, "水务": 0.5, "公路": 0.5,
            "券商": 1.2, "多元金融": 1.2,
            "半导体": 1.3, "芯片": 1.3, "软件": 1.3, "电子": 1.2,
            "AI": 1.4, "人工智能": 1.4, "通信": 1.2,
            "传媒": 1.5, "游戏": 1.5, "影视": 1.5, "教育": 1.3,
        }
        mult = 1.0
        if industry:
            for key, val in industry_multiplier.items():
                if key in industry:
                    mult = val
                    break
        return round(base * mult, 3)

    # ── P0-1: 客观财务因子自动风险标记 ──
    def _apply_auto_risk_overrides(self, stock_list: list, review_report: str,
                                    market_state: str) -> list:
        """
        基于客观财务因子/报告关键词自动生成风险标记。
        纯规则驱动，不调用LLM。
        """
        flags = []
        for s in stock_list:
            code = s.get("代码", s.get("code", ""))
            name = s.get("名称", s.get("name", ""))
            pe = s.get("PE", s.get("市盈率", 0))
            score = s.get("综合评分", s.get("score", 0))

            # 因子1：PE为负（亏损股）→ 自动high
            try:
                pe_val = float(pe) if pe else 0
                if pe_val < 0:
                    flags.append({
                        "code": code, "name": name,
                        "severity": "high",
                        "reason": f"【自动】{name}({code})当前PE={pe_val}<0，连续亏损，扣非利润真实性需核查"
                    })
                    continue
                elif pe_val > 100:
                    flags.append({
                        "code": code, "name": name,
                        "severity": "high",
                        "reason": f"【自动】{name}({code})当前PE={pe_val}，估值显著高于行业合理范围，未盈利预期已充分定价"
                    })
                    continue
            except (ValueError, TypeError):
                pass

        # 因子2：从审查报告提取已知风险关键词
        if review_report:
            risk_keywords = {
                "质押": ("high", None),
                "问询函": ("high", None),
                "监管警示": ("veto", "监管处罚"),
                "诉讼": ("high", None),
                "减持计划": ("high", None),
                "商誉": ("high", None),
                "立案": ("veto", "立案调查"),
            }
            for s in stock_list:
                code = s.get("代码", s.get("code", ""))
                name = s.get("名称", s.get("name", ""))
                # 跳过已因PE触发标记的标的
                if any(f["code"] == code for f in flags):
                    continue
                for keyword, (sev, veto_reason) in risk_keywords.items():
                    key_pos = review_report.find(keyword)
                    code_pos = review_report.find(code)
                    name_pos = review_report.find(name)
                    if key_pos >= 0 and (code_pos >= 0 or name_pos >= 0):
                        # 关键词出现在该股票分析范围内（500字符内）
                        if (code_pos >= 0 and abs(key_pos - code_pos) < 500) or \
                           (name_pos >= 0 and abs(key_pos - name_pos) < 500):
                            flag = {
                                "code": code, "name": name,
                                "severity": sev,
                                "reason": f"【自动】{name}({code})报告含关键词「{keyword}」"
                            }
                            if veto_reason:
                                flag["veto_reason"] = veto_reason
                            flags.append(flag)
                            break

        return flags

    def _get_market_state_from_index(self) -> str:
        """基于沪深300收盘价 vs 20日均线判断市场强弱。
        
        5档市场状态（P0-3升级）：
        - 偏多  | ratio >= +3%
        - 震荡偏强 | +1% ~ +3%
        - 震荡  | -1% ~ +1%
        - 震荡偏弱 | -3% ~ -1%
        - 偏空  | ratio <= -3%
        返回 5 档状态字符串，供动态阻塞阈值和LLM prompt使用。
        """
        try:
            from market_agent import fetch_quotes
            quotes = fetch_quotes(["sh000300"])  # 沪深300
            if not quotes:
                return "震荡"
            quote = quotes[0]
            price = quote.get("现价", 0)
            ma20 = quote.get("MA20", 0) or quote.get("ma20", 0)
            if price and ma20:
                ratio = price / ma20 - 1
                if ratio >= 0.03:
                    return "偏多"
                elif ratio >= 0.01:
                    return "震荡偏强"
                elif ratio <= -0.03:
                    return "偏空"
                elif ratio <= -0.01:
                    return "震荡偏弱"
            return "震荡"
        except Exception:
            return "震荡"

    def _parse_challenges(self, text: str) -> list:
        """解析JSON结果，支持多种格式"""
        challenges = []
        # 策略1：提取所有```json...``` 代码块
        for pattern in [r'```json\s*([\s\S]*?)\s*```', r'```\s*(\{[\s\S]*?\})\s*```']:
            matches = re.findall(pattern, text)
            for m in matches:
                try:
                    data = json.loads(m)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and ("challenges" in item or "code" in item):
                                challenges.append(item)
                    elif isinstance(data, dict):
                        if "challenges" in data or "stocks" in data:
                            # {"stocks": [{"code":..., "challenges":[...]}, ...]} 或
                            # {"challenges": [{"code":..., "challenges":[...]}, ...]}
                            stock_list = data.get("stocks") or data.get("challenges", [])
                            if isinstance(stock_list, list) and len(stock_list) > 0:
                                for item in stock_list:
                                    if isinstance(item, dict) and ("code" in item):
                                        challenges.append(item)
                            else:
                                challenges.append(data)
                        elif "code" in data:
                            challenges.append(data)
                except json.JSONDecodeError:
                    continue
        if challenges:
            return challenges

        # 策略2：提取所有独立JSON对象（兜底）

        # 提取多个JSON对象
        challenges = []
        stack, start, in_json = [], -1, False
        for i, ch in enumerate(text):
            if ch == '{':
                if not in_json:
                    in_json, start = True, i
                stack.append('{')
            elif ch == '}':
                if stack:
                    stack.pop()
                    if not stack and in_json:
                        try:
                            obj = json.loads(text[start:i+1])
                            if "stocks" in obj and isinstance(obj["stocks"], list):
                                challenges.extend(s for s in obj["stocks"] if isinstance(s, dict) and "code" in s)
                            elif "challenges" in obj or "code" in obj:
                                challenges.append(obj)
                        except json.JSONDecodeError:  # 安全降级: 单个质疑记录JSON解析失败→跳过该条，不影响整体
                            pass
                        in_json = False

        # 文本降级解析
        if not challenges:
            challenges = self._parse_text_fallback(text)
        return challenges

    def _parse_text_fallback(self, text: str) -> list:
        """文本解析降级"""
        challenges = []
        # 找 ## 标题或代码块
        blocks = re.split(r'##?\s*\[?(\d{6})\]?\s*', text)
        i = 1
        while i < len(blocks) - 1:
            code, block = blocks[i], blocks[i + 1][:300]
            name = "?"
            challenge = {"code": code, "name": name, "challenges": [], "overall_verdict": "challenge_required", "summary": block[:50]}
            severity = "high" if any(k in block for k in ["高风险", "重大", "关键"]) else "medium"
            for dim in ["驱动逻辑", "位置分析", "量能判断", "风险低估", "方案矛盾"]:
                challenge["challenges"].append({
                    "dimension": dim,
                    "question": f"需关注{dim}风险",
                    "severity": severity if dim in block else "low"
                })
            challenges.append(challenge)
            i += 2
        return challenges

    def _extract_high_risk(self, challenges: list) -> list:
        return [{
            "code": s.get("code", "?"),
            "name": s.get("name", "?"),
            "summary": s.get("summary", ""),
            "high_challenges": [c for c in s.get("challenges", []) if c.get("severity") in ("veto", "high")]
        } for s in challenges if any(c.get("severity") in ("veto", "high") for c in s.get("challenges", []))]

    def _generate_report(self, challenges: list, high_risk: list, raw: str) -> str:
        today = datetime.now().strftime("%Y-%m-%d %H:%M")
        total, high_count = len(challenges), len(high_risk)
        risk_emoji = "🟢" if high_count == 0 else ("🟡" if high_count <= total * 0.3 else "🔴")
        risk_level = "低风险" if high_count == 0 else ("中风险" if high_count <= total * 0.3 else "高风险")

        lines = [
            f"# 【质疑审查报告】{today}\n",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
            "## 📊 质疑概览\n",
            f"| 指标 | 数值 |\n|------|------|\n| 总股票数 | {total} |\n| 高风险股票 | {high_count} |\n| 风险等级 | {risk_emoji} {risk_level} |\n",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
            "## 🔴 高风险股票\n",
        ]

        for i, stock in enumerate(high_risk, 1):
            lines.append(f"### {i}. {stock['name']}（{stock['code']}）")
            lines.append(f"**摘要**：{stock.get('summary', '无')}\n")
            lines.append("**关键质疑**：")
            for c in stock.get("high_challenges", []):
                lines.append(f"- **{c.get('dimension', '?')}**（{c.get('severity', '?')}）：{c.get('question', '无')}")
            lines.append("")

        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
            "## 📋 完整质疑详情\n",
        ])

        for stock in challenges:
            code, name = stock.get("code", "?"), stock.get("name", "?")
            verdict = stock.get("overall_verdict", "challenge_required")
            emoji = "✅" if verdict == "pass" else "⚠️"
            lines.append(f"### {emoji} {name}（{code}）")
            lines.append(f"**判定**：{verdict}")
            lines.append(f"**摘要**：{stock.get('summary', '无')}\n")
            lines.append("| 维度 | 质疑内容 | 严重性 |")
            lines.append("|------|---------|--------|")
            for c in stock.get("challenges", []):
                sev = c.get("severity", "low")
                sev_emoji = "⛔" if sev == "veto" else ("🔴" if sev == "high" else ("🟡" if sev == "medium" else "🟢"))
                lines.append(f"| {c.get('dimension', '?')} | {c.get('question', '无')} | {sev_emoji} {sev} |")
            lines.append("\n---\n")

        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
            f"## 📝 LLM 原始输出\n```\n{raw[:2000]}\n```\n",
            f"*报告生成时间：{datetime.now().strftime('%H:%M:%S')}*",
        ])
        return "\n".join(lines)

    def _save_report(self, report: str) -> Path:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        filepath = self.history_dir / f"{datetime.now().strftime('%Y-%m-%d')}_质疑审查报告.md"
        self.safe_write_text(filepath, report)
        return filepath

    def _save_verdict(self, challenges: list, high_risk: list):
        """保存结构化裁决结果（二审制Gate使用）
        
        升级内容：
        - 任一维度 severity=veto → 直接 blocked
        - ≥3 个维度 severity=high → 自动 challenge_required（多中等风险叠加=高风险）
        - 输出 veto_count 字段供日志排查
        """
        # 获取市场状态（5档，P0-3升级）
        market_state = self._get_market_state_from_index()
        # ── P0-2: 维度加权阻塞 ──
        DIMENSION_WEIGHTS = {
            "位置分析": 1.5,   # 位置决定86%亏损，评分系统权重35%
            "风险低估": 1.3,   # 财务暴雷一票否决
            "驱动逻辑": 1.0,   # 中性
            "量能判断": 1.0,   # 中性
            "方案矛盾": 0.5,   # 执行纪律问题，不决定方向
        }
        # 5档市场状态对应的加权阻塞阈值
        WEIGHTED_THRESHOLDS = {
            "偏多": 5.5,
            "震荡偏强": 5.0,
            "震荡": 4.0,
            "震荡偏弱": 3.0,
            "偏空": 2.0,
        }
        auto_block_threshold = WEIGHTED_THRESHOLDS.get(market_state, 4.0)

        self.history_dir.mkdir(parents=True, exist_ok=True)

        blocked = []
        passed = []
        for s in challenges:
            code = s.get("code", "?")
            name = s.get("name", "?")
            verdict = s.get("overall_verdict", "challenge_required")
            challenges_list = s.get("challenges", [])
            has_veto = any(c.get("severity") == "veto" for c in challenges_list)
            raw_high_count = sum(1 for c in challenges_list if c.get("severity") in ("veto", "high"))
            weighted_count = sum(
                DIMENSION_WEIGHTS.get(c.get("dimension", ""), 1.0)
                for c in challenges_list if c.get("severity") in ("veto", "high")
            )
            has_high = raw_high_count > 0

            # P0升级：veto一票否决
            if has_veto:
                verdict = "challenge_required"
                block_reason = "veto"
            # P0-2: 维度加权阻塞阈值
            elif weighted_count >= auto_block_threshold and verdict == "pass":
                verdict = "challenge_required"
                block_reason = "high_threshold"
            else:
                block_reason = ""

            item = {"code": code, "name": name, "verdict": verdict, "has_high_risk": has_high, "veto_count": sum(1 for c in challenges_list if c.get("severity") == "veto"), "high_count": raw_high_count, "weighted_count": round(weighted_count, 1), "block_reason": block_reason}
            if verdict == "challenge_required":
                blocked.append(item)
            else:
                passed.append(item)
        verdict_data = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "total": len(challenges),
            "total_veto": sum(1 for s in challenges if any(c.get("severity") == "veto" for c in s.get("challenges", []))),
            "total_high": sum(1 for s in challenges if any(c.get("severity") in ("veto", "high") for c in s.get("challenges", []))),
            "passed": passed,
            "blocked": blocked,
            "gate_status": "blocked" if blocked else "passed",
        }
        filepath = self.history_dir / f"{datetime.now().strftime('%Y-%m-%d')}_质疑审查裁决.json"
        self.safe_write_json(filepath, verdict_data)
