#!/usr/bin/env python3
"""
天枢权衡 — LLM输出格式漂移检测模块 v2

检测LLM输出格式是否发生系统性漂移，导致正则提取失败。
检测对象：LLM prompt日志中的原始输出（logs/prompts/），而非报告文件。

核心检测逻辑：
1. 读取最近N天的prompt日志（ReviewAgent/DecisionAgent/SkepticAgent）
2. 对每份LLM原始输出，用代码库实际使用的正则模式进行提取测试
3. 统计正则提取成功率
4. 当成功率<50%时触发漂移告警
5. 记录漂移详情（失败模式、样本行）

用法:
    python scripts/llm_drift_detector.py                    # 检测最近3天
    python scripts/llm_drift_detector.py --days 7           # 检测最近7天
    python scripts/llm_drift_detector.py --verbose          # 详细输出
    python scripts/llm_drift_detector.py --alert            # 告警输出
"""

import os, sys, json, datetime, re, glob, argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
LOG_DIR = PROJECT_ROOT / "logs" / "prompts"
DQ_DIR = PROJECT_ROOT / "data" / "dq"
DQ_DIR.mkdir(parents=True, exist_ok=True)

NOW = datetime.datetime.now()
TODAY = datetime.date.today()

# 代码库实际使用的正则模式（来自 review_agent.py 和 decision_agent.py）
EXTRACTION_PATTERNS = {
    "review_score": {
        "name": "审查评分提取",
        "patterns": [
            r"综合评分[：:\s]*\[?\*?\s*(\d+)",
            r"综合(?:分|评分)\s*[：:\s]*\*?\s*(\d+)",
            r"(?:评分|得分)[：:\s]*\*?\s*(\d+)\s*分",
            r"[（(]\s*(\d+)\s*分\s*[)）]",
            r"(\d+)\s*分[，,。.\s]*(?:综合|四维|审查)",
            r"(?:综合|标的|审查)?评分[：:\s]*\*?\s*(\d+)(?:\s*分)?",
            r"\|\s*\*{0,2}综合评分\*{0,2}\s*\|\s*\*{0,2}(\d+)\*{0,2}",
        ],
        "expected": "应包含综合评分：XX的格式"
    },
    "review_flow": {
        "name": "审查流向判断",
        "patterns": [
            r"→\s*(→)?\s*升级",
            r"→\s*(→)?\s*降级",
            r"→\s*(→)?\s*保留",
            r"流向[：:]\s*(升级|保留|降级|淘汰)",
        ],
        "expected": "应包含→升级/→降级等流向标记"
    },
    "decision_picks": {
        "name": "决策主推标的",
        "patterns": [
            r"【(主推|备选|关注|推荐)\s*】?\s*([\u4e00-\u9fa5]{2,8})\s*[（(](\d{6})[）)]",
            r"【主推】|【备选】",
        ],
        "expected": "应包含【主推】StockName(Code) 格式"
    },
    "decision_trade_info": {
        "name": "决策交易信息",
        "patterns": [
            r"单笔仓位[：:]\s*(\d+(?:\.\d+)?)%",
            r"止损[线触发]*[：:]\s*([\d.]+)",
            r"第一目标[价]*[：:]\s*([\d.]+)",
            r"触发条件[：:]\s*([\d.]+)\s*(?:元|块|价)",
            r"买入方式[：:]\s*([^\n]+)",
        ],
        "expected": "应包含仓位/止损/止盈/触发条件"
    },
    "skeptic_verdict": {
        "name": "质疑裁决",
        "patterns": [
            r"overall_verdict[：:]\s*(pass|challenge_required|fail)",
            r"裁决[：:]\s*(通过|挑战|否决)",
            r"veto_count|high_count|weighted_count",
        ],
        "expected": "应包含裁决结果或计数"
    },
}


def find_prompt_logs(days: int = 3) -> list[Path]:
    """查找最近N天的prompt日志"""
    logs = []
    for d in range(days):
        date_str = (TODAY - datetime.timedelta(days=d)).strftime("%Y-%m-%d")
        for f in LOG_DIR.glob(f"*{date_str}*.md"):
            logs.append(f)
        for f in LOG_DIR.glob(f"*{date_str}*.txt"):
            logs.append(f)
    return sorted(logs, reverse=True)


def extract_llm_response(log_content: str) -> str:
    """
    从prompt日志中提取LLM的原始输出（响应部分）。
    通常prompt日志包含"--- Prompt ---"和"--- Response ---"分隔。
    """
    # 尝试多种分隔符
    for sep in ["--- Response ---", "--- 响应 ---", "## Response", "## 响应", "LLM输出:", "LLM Response:"]:
        if sep in log_content:
            return log_content.split(sep, 1)[-1].strip()
    # 如果有"## 开始"或"---"后的大段文本
    parts = log_content.split("---")
    if len(parts) >= 3:
        return parts[-1].strip()
    return log_content  # 无法分割，全量返回


def detect_drift_in_log(log_path: Path, patterns: list[str]) -> dict:
    """
    检测单份prompt日志的格式漂移
    """
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {"file": log_path.name, "error": "无法读取", "success_rate": 0}

    # 提取LLM响应部分
    response = extract_llm_response(content)
    if not response or len(response) < 50:
        return {"file": log_path.name, "error": "响应太短或无响应", "success_rate": 0}

    # 按行分割，检查关键行
    lines = [l.strip() for l in response.split("\n") if l.strip() and len(l.strip()) > 10]
    target_lines = lines  # 所有非空行都检查

    matched = 0
    unmatched_samples = []
    matched_samples = []

    for line in target_lines:
        is_matched = False
        for pat in patterns:
            if re.search(pat, line):
                is_matched = True
                break
        if is_matched:
            matched += 1
            if len(matched_samples) < 3:
                matched_samples.append(line[:80])
        else:
            if len(unmatched_samples) < 5:
                unmatched_samples.append(line[:80])

    total = len(target_lines)
    rate = (matched / total * 100) if total > 0 else 0

    return {
        "file": log_path.name,
        "total_lines": total,
        "matched_lines": matched,
        "success_rate": round(rate, 1),
        "sample_unmatched": unmatched_samples,
        "sample_matched": matched_samples,
    }


def scan_drift(days: int = 3) -> dict:
    """扫描最近N天的prompt日志，检测格式漂移"""
    results = {
        "scan_time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "scan_days": days,
        "groups": {},
        "drift_detected": False,
        "drift_count": 0,
        "total_logs": 0,
        "alerts": [],
    }

    logs = find_prompt_logs(days)
    all_log_names = [f.name for f in logs]

    for group_key, group_info in EXTRACTION_PATTERNS.items():
        patterns = group_info["patterns"]
        group_results = {
            "name": group_info["name"],
            "logs": [],
            "total_lines": 0,
            "total_matched": 0,
            "overall_rate": 0.0,
        }

        # 根据组名过滤关联的prompt日志
        if "review" in group_key:
            relevant_logs = [f for f in logs if "ReviewAgent" in f.name]
        elif "decision" in group_key:
            relevant_logs = [f for f in logs if "DecisionAgent" in f.name]
        elif "skeptic" in group_key:
            relevant_logs = [f for f in logs if "SkepticAgent" in f.name]
        else:
            relevant_logs = logs

        results["total_logs"] += len(relevant_logs)

        for log_path in relevant_logs:
            dr = detect_drift_in_log(log_path, patterns)
            if "error" not in dr:
                group_results["logs"].append(dr)
                group_results["total_lines"] += dr["total_lines"]
                group_results["total_matched"] += dr["matched_lines"]

        # 计算整体匹配率
        total = group_results["total_lines"]
        matched = group_results["total_matched"]
        group_results["overall_rate"] = round((matched / total * 100), 1) if total > 0 else 100.0

        # 漂移判定
        rate = group_results["overall_rate"]
        if rate < 50:
            results["drift_detected"] = True
            results["drift_count"] += 1
            results["alerts"].append({
                "level": "critical",
                "group": group_info["name"],
                "rate": rate,
                "message": f"LLM格式漂移严重: {group_info['name']} 匹配率仅{rate}%"
            })
        elif rate < 60:
            results["alerts"].append({
                "level": "warning",
                "group": group_info["name"],
                "rate": rate,
                "message": f"LLM格式轻微偏离: {group_info['name']} 匹配率{rate}%"
            })

        # 收集未匹配样本
        all_unmatched = []
        for r in group_results["logs"]:
            all_unmatched.extend(r.get("sample_unmatched", []))
        group_results["sample_unmatched"] = all_unmatched[:5]

        results["groups"][group_key] = group_results

    # 计算总体评分
    total_rate = 0
    group_count = 0
    for gk, gr in results["groups"].items():
        if gr["total_lines"] > 0:
            total_rate += gr["overall_rate"]
            group_count += 1
    results["overall_rate"] = round(total_rate / group_count, 1) if group_count > 0 else 100.0
    results["health"] = "healthy" if results["overall_rate"] >= 80 else ("warning" if results["overall_rate"] >= 60 else "critical")

    return results


def main():
    parser = argparse.ArgumentParser(description="天枢LLM格式漂移检测 v2")
    parser.add_argument("--days", type=int, default=3, help="检测天数范围（默认3天）")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    parser.add_argument("--alert", action="store_true", help="告警输出")
    args = parser.parse_args()

    result = scan_drift(days=args.days)

    # 保存结果
    result_file = DQ_DIR / f"llm_drift_{TODAY.isoformat()}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 输出
    if args.alert:
        for a in result.get("alerts", []):
            le = "🔴" if a["level"] == "critical" else "🟡"
            print(f"{le} [LLM格式漂移] {a['message']}")
    elif args.verbose:
        health_emoji = {"healthy": "✅", "warning": "🟡", "critical": "🔴"}
        print(f"📊 LLM格式漂移检测 v2 | {result['scan_time']}")
        print(f"  扫描范围: 最近{result['scan_days']}天, {result['total_logs']}份prompt日志")
        print(f"  总体健康度: {health_emoji.get(result['health'], '❓')} {result['health'].upper()} (匹配率{result['overall_rate']}%)")
        print(f"  漂移告警: {result['drift_count']}项")
        print()

        for gk, gr in result["groups"].items():
            emoji = "✅" if gr["overall_rate"] >= 80 else ("🟡" if gr["overall_rate"] >= 60 else "🔴")
            print(f"  {emoji} {gr['name']}: {gr['overall_rate']}% ({gr['total_matched']}/{gr['total_lines']})")
            if gr.get("sample_unmatched"):
                print(f"     未匹配样本:")
                for s in gr["sample_unmatched"][:3]:
                    print(f"       → {s[:60]}")
            print()

        if result["alerts"]:
            print(f"  📢 告警 ({len(result['alerts'])}条):")
            for a in result["alerts"]:
                le = "🔴" if a["level"] == "critical" else "🟡"
                print(f"    {le} {a['message']}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()