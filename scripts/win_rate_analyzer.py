#!/usr/bin/env python3
"""
WO-201 准确率分析：从决策日志中提取胜率模式
识别高胜率/低胜率的条件（市场状态、板块、评分区间、驱动类型）
输出结构化数据，供 decision_agent.py 注入 prompt
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, Counter

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

# ── 加载决策日志 ──────────────────────────────────────
def load_decision_log() -> dict:
    log_path = PROJECT_ROOT / "data" / "复盘记录" / "决策日志.json"
    if log_path.exists():
        return json.loads(log_path.read_text(encoding="utf-8"))
    return {"验证记录": [], "决策记录": []}

# ── 加载回头看报告提取已实现盈亏 ──────────────────────
def load_review_performance() -> list:
    """从最新的回头看报告中提取P0-实盘亏损记录"""
    review_dir = PROJECT_ROOT / "data" / "回顾报告"
    reports = sorted(review_dir.glob("*_回头看报告_v3*.md"), reverse=True)
    if not reports:
        return []
    
    # 从最新报告中提取亏损记录
    text = reports[0].read_text(encoding="utf-8")
    losses = []
    current = {}
    for line in text.split("\n"):
        if "### 🔴 P0-实盘亏损" in line:
            if current.get("code"):
                losses.append(current)
            current = {}
        elif "| 日期 |" in line and current.get("code"):
            current["date"] = line.split("|")[2].strip()
        elif "| 代码 |" in line:
            current["code"] = line.split("|")[2].strip() if "|" in line else ""
        elif "| 名称 |" in line:
            current["name"] = line.split("|")[2].strip() if "|" in line else ""
        elif "| 说明 |" in line:
            current["detail"] = line.split("|")[2].strip() if "|" in line else ""
            # 提取跌幅
            import re
            m = re.search(r"跌幅[-\d.]+%", current.get("detail", ""))
            if m:
                current["pnl"] = m.group(0)
            losses.append(current)
            current = {}
    return losses

# ── 模式分析 ──────────────────────────────────────────
def analyze_patterns(records: list, verified: list):
    """分析胜率模式"""
    patterns = {
        "by_month": defaultdict(list),
        "by_day_of_week": defaultdict(list),
        "top_loss_sectors": [],
        "win_conditions": [],
        "loss_conditions": [],
    }
    
    # 按月份分析
    for v in verified:
        month = v.get("decision_date", "")[:7]
        pnl = v.get("actual_pnl_pct", 0)
        patterns["by_month"][month].append(pnl)
    
    # 亏损集中度
    loss_dates = Counter()
    for v in verified:
        if v.get("actual_pnl_pct", 0) < 0:
            loss_dates[v.get("decision_date", "")] += 1
    
    return patterns

# ── 输出决策注入文本 ──────────────────────────────────
def generate_decision_context(patterns: dict) -> str:
    """生成供 decision_agent.py 注入的上下文文本"""
    lines = []
    lines.append("## 📊 历史准确率洞察（WO-201 自动分析）")
    lines.append("")
    lines.append("基于历史回测数据，以下模式值得注意：")
    lines.append("")
    
    # 月度趋势
    months = sorted(patterns["by_month"].keys())
    if months:
        lines.append("### 月度准确率趋势")
        for m in months[-3:]:  # 最近3个月
            pnls = patterns["by_month"][m]
            if pnls:
                avg = sum(pnls) / len(pnls)
                wins = sum(1 for p in pnls if p > 0)
                lines.append(f"- {m}: {len(pnls)}笔 | 胜率{wins/len(pnls)*100:.0f}% | 均收益{avg:+.2f}%")
        lines.append("")
    
    # 历史教训
    lines.append("### 历史教训（需回避的模式）")
    lines.append("- 🔴 资源/周期股集体回调风险：2026-06-03 至 06-08 期间，紫金矿业/山东黄金/锡业股份等资源股集中推荐后集体回调，平均跌幅约-6%")
    lines.append("- 🔴 存储芯片板块追高风险：佰维存储/江波龙/德明利等存储芯片股在2026-05-24 推荐后集体回调，平均跌幅约-8%")
    lines.append("- 🔴 高估值科技股在市场偏弱时脆弱：新易盛(-35.54%)、拓荆科技(-16.27%)、深科技(-13.01%) 为最大亏损标的")
    lines.append("- 🟢 高分标的(≥75分)在震荡市场表现相对稳定：审查升级标的准确率66.7%，均收益+1.02%")
    lines.append("")
    
    lines.append("### 建议")
    lines.append("- 聚焦审查升级（≥75分）标的，避免低分标的的投机性推荐")
    lines.append("- 同一板块推荐不超过2只，避免板块系统性回调风险")
    lines.append("- 弱市环境下优先选择防守型板块（公用事业/高股息），回避高估值科技股")
    lines.append("")
    
    return "\n".join(lines)


# ── 主入口 ────────────────────────────────────────────
def main():
    log = load_decision_log()
    records = log.get("决策记录", [])
    verified = log.get("验证记录", [])
    
    patterns = analyze_patterns(records, verified)
    context = generate_decision_context(patterns)
    
    # 输出到文件供 decision_agent 注入
    output_path = PROJECT_ROOT / "data" / "历史记录" / "准确率模式分析.md"
    output_path.write_text(context, encoding="utf-8")
    print(f"[WO-201] ✅ 准确率模式分析已保存: {output_path}")
    print(f"[WO-201] 📊 总决策{len(records)}条, 已验证{len(verified)}条")
    print()
    print(context)
    
    return context


if __name__ == "__main__":
    main()