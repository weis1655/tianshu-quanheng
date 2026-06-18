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
- **震荡偏强/偏多（强市）**：趋势延续性优先。仅放宽"位置分析-估值分位"的质疑标准（high→medium），量能/风险维度保持严格。
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

            lines = ["| 代码 | 名称 | 现价 | 涨跌 | 换手率 | 量比 | 市值 | 换手阈值 |", "|------|------|------|------|--------|------|------|--------|"]
            for s in stock_list:
                code = s.get("股票代码", s.get("代码", s.get("code", "")))
                name = s.get("股票名称", s.get("名称", s.get("name", "?")))
                q = qmap.get(code, {})
                price = q.get("现价", "—")
                chg = q.get("涨跌幅", 0)
                turnover = q.get("换手率", 0)
                vol_ratio = q.get("量比", 0)
                market_cap = q.get("流通市值", 0) or q.get("总市值", 0)
                threshold = self.get_turnover_threshold(market_cap) if isinstance(market_cap, (int, float)) and market_cap > 0 else "—"
                threshold_str = f"{threshold*100:.1f}%" if isinstance(threshold, float) else "—"
                market_cap_str = f"{market_cap:.1f}亿" if isinstance(market_cap, (int, float)) and market_cap > 0 else "—"
                chg_str = f"{chg:+.2f}%" if isinstance(chg, float) else str(chg)
                lines.append(f"| {code} | {name} | {price} | {chg_str} | {turnover:.2f}% | {vol_ratio:.2f} | {market_cap_str} | {threshold_str} |")
            return "\n".join(lines)
        except Exception:
            return "（行情获取失败）"

    @staticmethod
    def get_turnover_threshold(market_cap: float) -> float:
        """根据市值（亿元）返回换手率健康阈值。
        
        千亿以上大盘股 >5% 算异常
        300-1000亿中盘股 >8% 算异常
        100-300亿中小盘 >12% 算异常
        100亿以下小盘 >18% 算异常
        """
        if market_cap >= 1000:
            return 0.05  # 5%
        elif market_cap >= 300:
            return 0.08  # 8%
        elif market_cap >= 100:
            return 0.12  # 12%
        else:
            return 0.18  # 18%

    def _get_market_state_from_index(self) -> str:
        """基于沪深300收盘价 vs 20日均线判断市场强弱。
        
        返回: '偏强' / '偏弱'
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
                if ratio >= 0.01:
                    return "偏强"
                elif ratio <= -0.01:
                    return "偏弱"
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
                        if "challenges" in data:
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
                            if "challenges" in obj or "code" in obj:
                                challenges.append(obj)
                        except json.JSONDecodeError:
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
        self.history_dir.mkdir(parents=True, exist_ok=True)
        blocked = []
        passed = []
        for s in challenges:
            code = s.get("code", "?")
            name = s.get("name", "?")
            verdict = s.get("overall_verdict", "challenge_required")
            challenges_list = s.get("challenges", [])
            has_veto = any(c.get("severity") == "veto" for c in challenges_list)
            high_count = sum(1 for c in challenges_list if c.get("severity") in ("veto", "high"))
            has_high = high_count > 0

            # P0升级：veto一票否决
            if has_veto:
                verdict = "challenge_required"
            # P0升级：≥3个high叠加=高风险
            elif high_count >= 3 and verdict == "pass":
                verdict = "challenge_required"

            item = {"code": code, "name": name, "verdict": verdict, "has_high_risk": has_high, "veto_count": sum(1 for c in challenges_list if c.get("severity") == "veto"), "high_count": high_count}
            if verdict == "challenge_required" or has_high:
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
