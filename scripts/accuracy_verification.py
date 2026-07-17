#!/usr/bin/env python3
"""
天枢权衡 · 全维度准确率基线测试脚本
无随机因子，确定性回测，输出完整基线指标

用法：
  python scripts/accuracy_verification.py                 # 默认近90天基线
  python scripts/accuracy_verification.py --days 180      # 近180天
  python scripts/accuracy_verification.py --compare        # 对比基线
"""

import sys, json, re, os
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "agents"))
from path_config import ensure_agent_paths; ensure_agent_paths()
from safe_file_utils import safe_read_json

REPORT_DIR = PROJECT_ROOT / "data" / "准确率验证"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 1. 数据加载
# ═══════════════════════════════════════════════════════════════

def load_decision_log() -> list:
    """加载标准化决策日志（去重）"""
    data = safe_read_json(PROJECT_ROOT / "data" / "decision_log.json", default=[])
    if not isinstance(data, list):
        return []
    # ACC-04: 按(code+date)去重，保留last occurrence
    seen = set()
    deduped = []
    for r in data:
        key = f"{r.get('code','')}_{r.get('date','')}"
        if key not in seen:
            seen.add(key)
            # ACC-05: 填充缺失的source字段
            if not r.get('source') or r.get('source') == 'unknown':
                r['source'] = 'decision_log'
            deduped.append(r)
    return deduped


def load_chinese_decision_log() -> list:
    """加载中文格式决策日志"""
    path = PROJECT_ROOT / "data" / "复盘记录" / "决策日志.json"
    data = safe_read_json(path, default={})
    return data.get("决策记录", [])


def load_lookback_reports(days: int = 30) -> list:
    """加载回头看报告，提取准确率数据"""
    review_dir = PROJECT_ROOT / "data" / "回顾报告"
    reports = []
    cutoff = datetime.now() - timedelta(days=days)
    for f in sorted(review_dir.glob("*_回头看报告_v3*.md"), reverse=True):
        try:
            fdate = datetime.strptime(f.stem[:10], "%Y-%m-%d")
            if fdate < cutoff:
                continue
            text = f.read_text(encoding="utf-8")
            reports.append({"date": f.stem[:10], "text": text, "path": f})
        except (ValueError, OSError):
            continue
    return reports


# ═══════════════════════════════════════════════════════════════
# 2. 维度一：选股准确率
# ═══════════════════════════════════════════════════════════════

def verify_selection_accuracy(records: list, lookback_reports: list) -> dict:
    """校验选股准确率"""
    result = {
        "total_records": len(records),
        "score_distribution": defaultdict(int),
        "score_win_rate": {},
        "target_hit_rate": 0,
        "top_n_hit_rate": 0,
        "issues": [],
    }

    # 评分分布与各分段胜率
    score_bins = [(0, 30), (30, 50), (50, 60), (60, 70), (70, 75), (75, 80), (80, 90), (90, 100)]
    for lo, hi in score_bins:
        recs = [r for r in records if lo <= (r.get("tech_score", 0) or 0) < hi]
        if recs:
            has_pnl = [r for r in recs if r.get("actual_pnl") not in (None, 0, "", 0.0)]
            if has_pnl:
                wins = sum(1 for r in has_pnl if (r.get("actual_pnl", 0) or 0) > 0)
                avg_pnl = sum(r.get("actual_pnl", 0) or 0 for r in has_pnl) / len(has_pnl)
                result["score_win_rate"][f"{lo}-{hi}"] = {
                    "total": len(recs), "verified": len(has_pnl),
                    "wins": wins, "win_rate": round(wins / len(has_pnl) * 100, 1),
                    "avg_pnl": round(avg_pnl, 2),
                }

    # 总体选股准确率
    all_verified = [r for r in records if r.get("actual_pnl") not in (None, 0, "", 0.0)]
    if all_verified:
        total_wins = sum(1 for r in all_verified if (r.get("actual_pnl", 0) or 0) > 0)
        result["selection_accuracy"] = {
            "verified": len(all_verified),
            "wins": total_wins,
            "win_rate": round(total_wins / len(all_verified) * 100, 1),
            "avg_pnl": round(sum(r.get("actual_pnl", 0) or 0 for r in all_verified) / len(all_verified), 2),
        }

    # 回头看报告中的P0漏检统计
    p0_counts = defaultdict(int)
    for rp in lookback_reports:
        text = rp["text"]
        for m in re.finditer(r"### 🔴 P0-(\S+)", text):
            p0_type = m.group(1)
            p0_counts[p0_type] += 1
    result["p0_issues"] = dict(p0_counts)

    return result


# ═══════════════════════════════════════════════════════════════
# 3. 维度二：风控规则准确率
# ═══════════════════════════════════════════════════════════════

def verify_risk_control_accuracy(records: list, lookback_reports: list) -> dict:
    """校验风控规则准确率"""
    result = {
        "stop_loss_accuracy": {},
        "overheat_detection": {},
        "downgrade_accuracy": {},
        "issues": [],
    }

    # 止损触发精度
    all_pnl = [r for r in records if r.get("actual_pnl") not in (None, 0, "", 0.0)]
    if all_pnl:
        stopped = [r for r in all_pnl if (r.get("actual_pnl", 0) or 0) <= -5]
        missed = [r for r in all_pnl if (r.get("actual_pnl", 0) or 0) < -5]
        result["stop_loss_accuracy"] = {
            "total_stopped": len(stopped),
            "total_losses": len([r for r in all_pnl if (r.get("actual_pnl", 0) or 0) < 0]),
            "stop_loss_rate": round(len(stopped) / len(all_pnl) * 100, 1) if all_pnl else 0,
        }

    # 过热检测遗漏
    p0_overheat = 0
    total_p0 = 0
    for rp in lookback_reports:
        text = rp["text"]
        p0_overheat += len(re.findall(r"过热漏检", text))
        total_p0 += len(re.findall(r"P0-", text))
    result["overheat_detection"] = {
        "overheat_miss_count": p0_overheat,
        "total_p0_count": total_p0,
        "overheat_miss_rate": round(p0_overheat / total_p0 * 100, 1) if total_p0 > 0 else 0,
    }

    # 降级延迟统计
    downgrade_delays = 0
    for rp in lookback_reports:
        text = rp["text"]
        downgrade_delays += len(re.findall(r"P0-降级延迟", text))
    result["downgrade_accuracy"] = {
        "downgrade_delay_count": downgrade_delays,
        "avg_daily_delays": round(downgrade_delays / max(len(lookback_reports), 1), 1),
    }

    return result


# ═══════════════════════════════════════════════════════════════
# 4. 维度三：全链路一致性
# ═══════════════════════════════════════════════════════════════

def verify_full_chain_consistency(records: list, lookback_reports: list) -> dict:
    """校验全链路一致性"""
    result = {
        "data_quality": {},
        "decision_counts": {},
        "issues": [],
    }

    # 数据质量
    all_pnl = [r for r in records if r.get("actual_pnl") not in (None, 0, "", 0.0)]
    zero_pnl = [r for r in records if r.get("actual_pnl") in (None, 0, "", 0.0)]
    result["data_quality"] = {
        "total_records": len(records),
        "verified": len(all_pnl),
        "unverified": len(zero_pnl),
        "verification_rate": round(len(all_pnl) / len(records) * 100, 1) if records else 0,
    }

    # 决策分布
    result["decision_counts"] = {
        "total_events": len(records),
        "by_source": defaultdict(int),
    }
    for r in records:
        result["decision_counts"]["by_source"][r.get("source", "unknown")] += 1
    result["decision_counts"]["by_source"] = dict(result["decision_counts"]["by_source"])

    # 回头看报告中的准确率数据
    latest_report = lookback_reports[0] if lookback_reports else None
    if latest_report:
        text = latest_report["text"]
        m = re.search(r"综合胜率.*?(\d+\.?\d*)%", text)
        if m:
            result["lookback_win_rate"] = float(m.group(1))

    return result


# ═══════════════════════════════════════════════════════════════
# 5. 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="天枢准确率验证")
    parser.add_argument("--days", type=int, default=90, help="回溯天数")
    parser.add_argument("--output", action="store_true", help="保存报告")
    args = parser.parse_args()

    print(f"🏛️ 天枢权衡 · 全维度准确率基线测试")
    print(f"  测试周期: 近{args.days}天")
    print(f"  执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 加载数据
    records = load_decision_log()
    cn_records = load_chinese_decision_log()
    lookback_reports = load_lookback_reports(days=args.days)

    print(f"\n📦 数据加载:")
    print(f"  决策日志(标准): {len(records)}条")
    print(f"  决策日志(中文): {len(cn_records)}条")
    print(f"  回头看报告: {len(lookback_reports)}份")

    # 维度一：选股准确率
    print(f"\n{'='*50}")
    print("📊 维度一：选股准确率")
    print("=" * 50)
    sel = verify_selection_accuracy(records, lookback_reports)
    if sel.get("selection_accuracy"):
        sa = sel["selection_accuracy"]
        print(f"  总记录: {sa['verified']}条已验证")
        print(f"  胜率: {sa['win_rate']}% ({sa['wins']}/{sa['verified']})")
        print(f"  均收益: {sa['avg_pnl']:+.2f}%")

    print(f"\n  评分分段胜率:")
    for rng, data in sorted(sel.get("score_win_rate", {}).items()):
        print(f"    {rng}分: {data['verified']}笔 胜率{data['win_rate']}% 均收益{data['avg_pnl']:+.2f}%")

    if sel.get("p0_issues"):
        print(f"\n  P0问题分布:")
        for ptype, count in sorted(sel["p0_issues"].items()):
            print(f"    🔴 P0-{ptype}: {count}次")

    # 维度二：风控准确率
    print(f"\n{'='*50}")
    print("🔒 维度二：风控规则准确率")
    print("=" * 50)
    rc = verify_risk_control_accuracy(records, lookback_reports)
    if rc.get("stop_loss_accuracy"):
        sla = rc["stop_loss_accuracy"]
        print(f"  止损触发: {sla['total_stopped']}笔")
        print(f"  止损率: {sla['stop_loss_rate']}%")
    if rc.get("overheat_detection"):
        oh = rc["overheat_detection"]
        print(f"  过热漏检: {oh['overheat_miss_count']}次/{oh['total_p0_count']}P0 ({oh['overheat_miss_rate']}%)")
    if rc.get("downgrade_accuracy"):
        da = rc["downgrade_accuracy"]
        print(f"  降级延迟: {da['downgrade_delay_count']}次 (日均{da['avg_daily_delays']})")

    # 维度三：全链路一致性
    print(f"\n{'='*50}")
    print("🔗 维度三：全链路一致性")
    print("=" * 50)
    fc = verify_full_chain_consistency(records, lookback_reports)
    if fc.get("data_quality"):
        dq = fc["data_quality"]
        print(f"  数据验证率: {dq['verification_rate']}% ({dq['verified']}/{dq['total_records']})")
    if fc.get("lookback_win_rate"):
        print(f"  回头看综合胜率: {fc['lookback_win_rate']}%")

    # 汇总报告
    print(f"\n{'='*50}")
    print("📋 基线测试汇总")
    print("=" * 50)
    baseline = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "test_window_days": args.days,
        "selection": {
            "win_rate": sel.get("selection_accuracy", {}).get("win_rate", 0),
            "verified_count": sel.get("selection_accuracy", {}).get("verified", 0),
            "avg_pnl": sel.get("selection_accuracy", {}).get("avg_pnl", 0),
            "p0_count": sum(sel.get("p0_issues", {}).values()),
        },
        "risk_control": {
            "stop_loss_rate": rc.get("stop_loss_accuracy", {}).get("stop_loss_rate", 0),
            "overheat_miss_rate": rc.get("overheat_detection", {}).get("overheat_miss_rate", 0),
            "downgrade_delays": rc.get("downgrade_accuracy", {}).get("downgrade_delay_count", 0),
        },
        "full_chain": {
            "verification_rate": fc.get("data_quality", {}).get("verification_rate", 0),
            "lookback_win_rate": fc.get("lookback_win_rate", 0),
        },
    }

    print(f"  选股胜率: {baseline['selection']['win_rate']}%")
    print(f"  风控过热漏检: {baseline['risk_control']['overheat_miss_rate']}%")
    print(f"  降级延迟: {baseline['risk_control']['downgrade_delays']}次")
    print(f"  数据验证率: {baseline['full_chain']['verification_rate']}%")

    if args.output:
        out_path = REPORT_DIR / f"基线_{datetime.now().strftime('%Y%m%d')}.json"
        out_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  💾 基线报告已保存: {out_path}")


if __name__ == "__main__":
    main()