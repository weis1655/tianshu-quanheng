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

## 五维质疑框架

### 维度1：驱动逻辑质疑
核心：利好是真实的、可验证的吗？
- 政策利好是否有官方文件？
- 业绩改善是否体现在报表？
- "主力建仓"有无量价配合？
⚠️ 质疑点：数据来源、时效性、可证伪性

### 维度2：位置分析质疑
核心：当前位置真的安全吗？
- 相比历史高点调整幅度够吗？
- 是"高位横盘"还是"低位启动"？
- 是否已透支利好预期？
⚠️ 质疑点：相对位置、估值合理性

### 维度3：量能判断质疑
核心：量能异动是主力介入还是诱多？
- 放量在低位还是高位？
- 持续性如何（单日vs多日）？
- 换手率是否异常（>10%需警惕）？
⚠️ 质疑点：量能持续性、换手率异常

### 维度4：风险低估质疑
核心：风险是否被充分考虑？
- 有无隐藏减持压力？
- 有无解禁盘即将到来？
- 有无商誉减值/监管处罚？
⚠️ 质疑点：潜在风险、被忽视的因素

### 维度5：方案矛盾质疑
核心：买入方案本身是否自洽？
- 买入条件与止损条件矛盾？
- 目标价位有技术支撑？
- 仓位与风险收益比匹配？
⚠️ 质疑点：逻辑自洽性、风控完备性

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【输出格式】（必须JSON，禁止开场白）

```json
{
  "code": "000001",
  "name": "股票名称",
  "challenges": [
    {"dimension": "驱动逻辑", "question": "质疑内容", "severity": "high"},
    {"dimension": "位置分析", "question": "质疑内容", "severity": "medium"},
    {"dimension": "量能判断", "question": "质疑内容", "severity": "low"},
    {"dimension": "风险低估", "question": "质疑内容", "severity": "high"},
    {"dimension": "方案矛盾", "question": "质疑内容", "severity": "medium"}
  ],
  "overall_verdict": "pass / challenge_required",
  "summary": "质疑摘要（≤50字）"
}
```

⚠️ 硬性要求：
1. 每只股票必须回答全部5个维度
2. severity: high/medium/low
3. high是关键风险点
4. overall_verdict: pass=通过, challenge_required=需解决
5. 每只股票分析不超过100字
"""


USER_PROMPT_TEMPLATE = """请对以下重点观察池股票进行五维质疑审查：

重点观察池股票：
{candidate_stocks}

市场宏观背景：
{market_context}

实时行情：
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

        # 构建提示词
        user_prompt = USER_PROMPT_TEMPLATE.format(
            candidate_stocks=candidate_stocks,
            market_context=market_text,
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

            lines = ["| 代码 | 名称 | 现价 | 涨跌 | 换手率 | 量比 |", "|------|------|------|------|--------|------|"]
            for s in stock_list:
                code = s.get("股票代码", s.get("代码", s.get("code", "")))
                name = s.get("股票名称", s.get("名称", s.get("name", "?")))
                q = qmap.get(code, {})
                price = q.get("现价", "—")
                chg = q.get("涨跌幅", 0)
                turnover = q.get("换手率", 0)
                vol_ratio = q.get("量比", 0)
                chg_str = f"{chg:+.2f}%" if isinstance(chg, float) else str(chg)
                lines.append(f"| {code} | {name} | {price} | {chg_str} | {turnover:.2f}% | {vol_ratio:.2f} |")
            return "\n".join(lines)
        except Exception:
            return "（行情获取失败）"

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
            "high_challenges": [c for c in s.get("challenges", []) if c.get("severity") == "high"]
        } for s in challenges if any(c.get("severity") == "high" for c in s.get("challenges", []))]

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
                sev_emoji = "🔴" if sev == "high" else ("🟡" if sev == "medium" else "🟢")
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
        """保存结构化裁决结果（二审制Gate使用）"""
        self.history_dir.mkdir(parents=True, exist_ok=True)
        blocked = []
        passed = []
        for s in challenges:
            code = s.get("code", "?")
            name = s.get("name", "?")
            verdict = s.get("overall_verdict", "challenge_required")
            has_high = any(c.get("severity") == "high" for c in s.get("challenges", []))
            item = {"code": code, "name": name, "verdict": verdict, "has_high_risk": has_high}
            if verdict == "challenge_required" or has_high:
                blocked.append(item)
            else:
                passed.append(item)
        verdict_data = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "total": len(challenges),
            "passed": passed,
            "blocked": blocked,
            "gate_status": "blocked" if blocked else "passed",
        }
        filepath = self.history_dir / f"{datetime.now().strftime('%Y-%m-%d')}_质疑审查裁决.json"
        self.safe_write_json(filepath, verdict_data)
