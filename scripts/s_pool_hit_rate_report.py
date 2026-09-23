"""S 级操作池历史命中率报告生成器。

背景：
    09-22 盟主指出 S 池长期机制问题：
    - 命中率 25.9%（7/32 命中≥3%），平均偏差 -7%
    - 高估值题材股在弱市脆弱，但阈值保护区不能改 S 池准入阈值

本脚本做什么：
    1. 调用 PoolManager.evaluate_s_pool_history() 得到逐条命中/偏差数据
    2. 用代码/名称关键词做**启发式**题材股/稳健股分类（不改任何阈值）
    3. 分题材/稳健子集统计命中率，输出 Markdown 报告
    4. 报告仅作观察用，供后续策略层（Review/Skeptic）加"弱市+高估值折扣"参考

使用：
    python scripts/s_pool_hit_rate_report.py
    python scripts/s_pool_hit_rate_report.py --json   # 只输出 JSON 摘要
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))


# ── 题材股 / 稳健股 启发式分类 ────────────────────────────────
# 说明：这些只是**报告标注**用的关键词，不参与任何评分/阈值决策。
TOPIC_KEYWORDS = [
    "国产替代", "国产", "AI", "算力", "光模块", "芯片", "半导体", "存储",
    "新能源", "光伏", "锂电", "电池", "储能", "机器人", "军工", "商业航天",
    "卫星", "消费电子", "汽车", "智能驾驶", "自动驾驶", "华为", "华为链",
    "大模型", "大语言", "AI应用", "生成式", "量子", "低空", "低空经济",
    "医药", "创新药", "中药", "医美", "白酒", "消费", "白酒链",
    "5G", "6G", "卫星通信", "华为鸿蒙", "车路云", "华为汽车",
    "人形", "机器人", "工业母机", "信创", "数据安全", "网络安全",
]
STABLE_KEYWORDS = [
    "银行", "保险", "券商", "电力", "高速公路", "石油", "煤炭", "钢铁",
    "化工", "有色", "水泥", "家电", "白电", "基建", "电信", "燃气", "水务",
]


def classify_stock(code: str, name: str) -> str:
    """返回 '题材' / '稳健' / '中性'。仅用于报告统计。"""
    text = f"{code} {name}"
    for kw in TOPIC_KEYWORDS:
        if kw in text:
            return "题材"
    for kw in STABLE_KEYWORDS:
        if kw in text:
            return "稳健"
    # 板块代码启发式：创业板/科创板倾向题材
    if code.startswith(("300", "688", "301")):
        return "题材"
    return "中性"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="只输出 JSON 摘要")
    ap.add_argument("--out", type=str, default=None, help="报告输出路径")
    args = ap.parse_args()

    from pool_manager import PoolManager
    pm = PoolManager()
    r = pm.evaluate_s_pool_history()

    if "error" in r:
        print(f"[ERROR] {r['error']}", file=sys.stderr)
        return 1

    details = r.get("details", [])
    if not details:
        print("[WARN] S 池历史无评价数据")
        return 0

    # ── 分类统计 ──────────────────────────────────────────────
    by_tag = defaultdict(list)
    for x in details:
        tag = classify_stock(x.get("代码", ""), x.get("名称", ""))
        x["分类"] = tag
        by_tag[tag].append(x)

    def summarize(sub: list) -> dict:
        total = len(sub)
        if total == 0:
            return {"total": 0}
        hits = sum(1 for x in sub if x.get("评价") == "命中")
        big_misses = sum(1 for x in sub if x.get("评价") == "偏差")
        avg = sum(x.get("涨跌幅", 0) for x in sub) / total
        return {
            "total": total,
            "hits": hits,
            "big_misses": big_misses,
            "hit_rate": round(hits / total * 100, 1),
            "avg_change": round(avg, 2),
        }

    tag_stats = {tag: summarize(sub) for tag, sub in by_tag.items()}

    # 极端偏差标的（涨跌幅 < -10%）
    extreme_misses = [x for x in details if x.get("涨跌幅", 0) < -10]
    extreme_misses.sort(key=lambda x: x.get("涨跌幅", 0))

    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evaluated": r.get("evaluated", 0),
        "overall_hit_rate": r.get("hit_rate", 0),
        "overall_avg_change": r.get("avg_change", 0),
        "overall_hits": r.get("hits", 0),
        "overall_misses": r.get("misses", 0),
        "by_tag": tag_stats,
        "extreme_misses_count": len(extreme_misses),
        "extreme_misses": extreme_misses,
        "topic_share": round(
            (tag_stats.get("题材", {}).get("total", 0) / max(r.get("evaluated", 1), 1)) * 100, 1
        ),
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    # ── Markdown 报告 ────────────────────────────────────────
    out_path = args.out or str(ROOT / "审计" / "S池历史命中率报告.md")
    lines = []
    lines.append("# S 级操作池历史命中率报告")
    lines.append("")
    lines.append(f"- 生成时间：{summary['generated_at']}")
    lines.append(f"- 评价样本：{summary['evaluated']} 条")
    lines.append(f"- 整体命中率：{summary['overall_hit_rate']}% "
                 f"（≥+3% 命中，<-3% 偏差）")
    lines.append(f"- 整体平均涨跌幅：{summary['overall_avg_change']}%")
    lines.append(f"- 题材股占比：{summary['topic_share']}%（启发式分类，仅供参考）")
    lines.append("")
    lines.append("## 分题材/稳健子集")
    lines.append("")
    lines.append("| 分类 | 样本 | 命中(≥+3%) | 大偏差(<-3%) | 命中率 | 平均涨跌幅 |")
    lines.append("|------|------|------------|--------------|--------|------------|")
    for tag in ["题材", "稳健", "中性"]:
        s = tag_stats.get(tag, {})
        if s.get("total", 0) == 0:
            continue
        lines.append(f"| {tag} | {s['total']} | {s.get('hits', 0)} | "
                     f"{s.get('big_misses', 0)} | {s['hit_rate']}% | "
                     f"{s['avg_change']}% |")
    lines.append("")
    lines.append("## 极端偏差标的（涨跌幅 < -10%）")
    lines.append("")
    if not extreme_misses:
        lines.append("（无）")
    else:
        lines.append("| 代码 | 名称 | 分类 | 入场价 | 最新价 | 涨跌幅 | 评价 |")
        lines.append("|------|------|------|--------|--------|--------|------|")
        for x in extreme_misses:
            lines.append(f"| {x.get('代码','')} | {x.get('名称','')} | "
                         f"{x.get('分类','')} | {x.get('入场价','')} | "
                         f"{x.get('最新价','')} | {x.get('涨跌幅','')}% | "
                         f"{x.get('评价','')} |")
    lines.append("")
    lines.append("## 结论与后续动作")
    lines.append("")
    lines.append("1. **命中率 ~25-28%，平均偏差 -7%**：S 池长期看是负期望，"
                 "尤其题材股在弱市脆弱。")
    lines.append("2. **阈值保护区不能改 S_POOL_MIN_SCORE**（当前 75，弱市 80/85）："
                 "本报告只作观察，不触发准入阈值变更。")
    lines.append("3. **可选后续动作**（09-22 盟主指示的轻量约束方向）：")
    lines.append("   - 在 `review_agent.py` 增加\"弱市+高估值\"评分折扣（不改阈值，"
                 "仅在弱市对 PE 高/创业板/科创板标的在评分阶段额外 -3~-5 分）。")
    lines.append("   - 或让 `skeptic_agent.py` 在弱市对题材股自动升级 severity（已有 _apply_auto_risk_overrides 通道）。")
    lines.append("4. 本报告仅作观察，S 池准入阈值维持不变。")
    lines.append("")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 报告已写入: {out_path}")
    print(json.dumps({
        "evaluated": summary["evaluated"],
        "hit_rate": summary["overall_hit_rate"],
        "avg_change": summary["overall_avg_change"],
        "topic_share": summary["topic_share"],
        "by_tag": summary["by_tag"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
