#!/usr/bin/env python3
"""
天枢权衡 — 数据质量扫描脚本

五维数据质量检测：完整性、准确性、及时性、一致性、有效性。
支持 --fix 模式自动修复。

用法:
    python scripts/dq_scanner.py             # 默认JSON输出
    python scripts/dq_scanner.py --verbose   # 详细文本报告
    python scripts/dq_scanner.py --alert     # 告警输出
    python scripts/dq_scanner.py --fix       # 自动修复
    python scripts/dq_scanner.py --fix --dry-run  # 预览修复
"""

import os, sys, json, datetime, glob, re, shutil, argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
POOLS_DIR = PROJECT_ROOT / "五池管理"
DATA_DIR = PROJECT_ROOT / "data"
DQ_DIR = DATA_DIR / "dq"
FIX_LOG = DQ_DIR / "dq_fix_log.jsonl"
DQ_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.date.today()
NOW = datetime.datetime.now()

# 报告类型清单
REPORT_TYPES = [
    "宏观前置分析", "快筛报告", "技术面分析", "审查报告",
    "质疑审查报告", "质疑审查裁决", "决策报告", "四段闭环汇总"
]

# 五池名称
POOL_NAMES = ["快筛候选池", "重点观察池", "重点观察池_历史池", "边缘池", "持仓池", "S级操作池"]

# 必填字段
REQUIRED_FIELDS = {
    "快筛候选池": ["股票代码", "股票名称", "评分", "纳入日期"],
    "重点观察池": ["股票代码", "股票名称", "评分", "纳入日期", "综合评分"],
    "S级操作池": ["股票代码", "股票名称", "评分", "综合评分", "纳入日期"],
    "边缘池": ["股票代码", "股票名称", "评分", "综合评分", "纳入日期"],
    "持仓池": ["股票代码", "股票名称", "持仓数量", "成本价", "纳入日期"],
}

# 字段别名映射（一致性检测）
FIELD_ALIASES = {
    "股票代码": ["代码", "code", "stock_code"],
    "股票名称": ["名称", "name", "stock_name"],
    "评分": ["score", "综合分", "tech_score"],
    "综合评分": ["综合分", "composite_score"],
    "纳入日期": ["日期", "entry_date", "add_date"],
}


def check_json_file(filepath: str) -> tuple[bool, any, str]:
    """检查JSON文件是否可解析，返回(是否成功, 数据, 错误信息)"""
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        return True, data, ""
    except json.JSONDecodeError as e:
        return False, None, f"JSON解析失败: {e}"
    except FileNotFoundError:
        return False, None, "文件不存在"
    except Exception as e:
        return False, None, str(e)


def check_pool_integrity(pool_name: str, data: dict) -> list[dict]:
    """检查池的完整性"""
    alerts = []
    stocks = data.get("stocks", [])
    required = REQUIRED_FIELDS.get(pool_name, [])

    for stock in stocks:
        code = stock.get("股票代码", stock.get("代码", stock.get("code", "?")))
        name = stock.get("股票名称", stock.get("名称", stock.get("name", "?")))
        for field in required:
            # 通过别名查找
            aliases = FIELD_ALIASES.get(field, [field])
            found = any(stock.get(a) is not None for a in aliases)
            if not found:
                alerts.append({
                    "level": "warning", "check": "完整性",
                    "detail": f"{pool_name}/{code} {name}: 缺少必填字段 `{field}`(别名: {aliases})"
                })

    # 统计信息检查
    stats = data.get("统计", {})
    count = stats.get("持仓数", stats.get("count", -1))
    if count != len(stocks):
        alerts.append({
            "level": "warning", "check": "完整性",
            "detail": f"{pool_name}: 统计数({count})与实际数({len(stocks)})不一致"
        })

    return alerts


def check_score_accuracy(pool_name: str, data: dict) -> list[dict]:
    """检查评分准确性"""
    alerts = []
    stocks = data.get("stocks", [])

    for stock in stocks:
        code = stock.get("股票代码", stock.get("代码", "?"))
        name = stock.get("股票名称", stock.get("名称", "?"))
        score = stock.get("评分", stock.get("score", stock.get("综合评分", stock.get("综合分", None))))
        entry_date = stock.get("纳入日期", "")

        if score is not None:
            try:
                score_val = float(score)
                if score_val < 0 or score_val > 100:
                    alerts.append({
                        "level": "critical", "check": "准确性-评分区间",
                        "detail": f"{pool_name}/{code} {name}: 评分{score_val}不在0-100范围"
                    })
                # score=0 且入池>3天
                if score_val == 0 and entry_date:
                    try:
                        days = (TODAY - datetime.datetime.strptime(entry_date, "%Y-%m-%d").date()).days
                        if days >= 3:
                            alerts.append({
                                "level": "critical", "check": "准确性-score=0",
                                "detail": f"{pool_name}/{code} {name}: score=0已{days}天，应降级"
                            })
                    except ValueError:
                        pass
            except (ValueError, TypeError):
                alerts.append({
                    "level": "warning", "check": "准确性-评分格式",
                    "detail": f"{pool_name}/{code} {name}: 评分格式无效: {score}"
                })

    return alerts


def check_duplicates(pool_name: str, data: dict, all_codes: dict) -> list[dict]:
    """检查重复标的"""
    alerts = []
    stocks = data.get("stocks", [])
    seen = set()

    for stock in stocks:
        code = stock.get("股票代码", stock.get("代码", ""))
        if not code:
            continue
        # 池内重复
        if code in seen:
            alerts.append({
                "level": "warning", "check": "准确性-重复",
                "detail": f"{pool_name}: {code} 存在重复条目"
            })
        seen.add(code)

        # 跨池重复
        if code in all_codes:
            prev_pool = all_codes[code]
            if prev_pool != pool_name and pool_name not in ("重点观察池_历史池",):
                alerts.append({
                    "level": "warning", "check": "准确性-跨池重复",
                    "detail": f"{code}: 同时存在于 {prev_pool} 和 {pool_name}"
                })
        else:
            all_codes[code] = pool_name

    return alerts


def check_timeliness(pool_name: str, data: dict) -> list[dict]:
    """检查数据及时性"""
    alerts = []
    stats = data.get("统计", {})
    update_date_str = stats.get("更新日期", "")

    if update_date_str:
        try:
            update_date = datetime.datetime.strptime(update_date_str.split()[0], "%Y-%m-%d").date()
            days_diff = (TODAY - update_date).days
            if days_diff > 1:
                alerts.append({
                    "level": "warning" if days_diff <= 3 else "critical",
                    "check": "及时性-数据陈旧",
                    "detail": f"{pool_name}: 最后更新于{update_date_str}，已{days_diff}天未更新"
                })
        except ValueError:
            pass
    else:
        alerts.append({
            "level": "warning", "check": "及时性",
            "detail": f"{pool_name}: 缺少更新日期"
        })

    return alerts


def check_code_validity(pool_name: str, data: dict) -> list[dict]:
    """检查股票代码有效性"""
    alerts = []
    stocks = data.get("stocks", [])

    for stock in stocks:
        code = stock.get("股票代码", stock.get("代码", ""))
        name = stock.get("股票名称", stock.get("名称", "?"))
        if code and not re.match(r"^\d{6}$", str(code)):
            alerts.append({
                "level": "warning", "check": "有效性-股票代码",
                "detail": f"{pool_name}/{code} {name}: 代码格式无效"
            })

    return alerts


def check_decision_log() -> list[dict]:
    """检查决策日志完整性"""
    alerts = []
    log_file = DATA_DIR / "decision_log.json"
    if not log_file.exists():
        return [{"level": "critical", "check": "完整性", "detail": "决策日志文件不存在"}]

    ok, data, err = check_json_file(str(log_file))
    if not ok:
        return [{"level": "critical", "check": "完整性", "detail": f"决策日志: {err}"}]

    if isinstance(data, list):
        count = len(data)
        if count == 0:
            alerts.append({"level": "critical", "check": "完整性", "detail": "决策日志为空"})

        # 检查是否包含今日记录
        today_str = TODAY.isoformat()
        today_records = [r for r in data if r.get("date") == today_str]
        if not today_records:
            alerts.append({"level": "info", "check": "及时性", "detail": "决策日志无今日记录"})

        # 检查记录完整性
        incomplete = [r for r in data if not r.get("code") or not r.get("name")]
        if incomplete:
            alerts.append({"level": "warning", "check": "完整性", "detail": f"决策日志有{len(incomplete)}条不完整记录"})
    else:
        alerts.append({"level": "warning", "check": "完整性", "detail": "决策日志格式非列表"})

    return alerts


def check_daily_reports() -> list[dict]:
    """检查当日报告是否完整"""
    alerts = []
    history_dir = DATA_DIR / "历史记录"
    if not history_dir.exists():
        return [{"level": "critical", "check": "完整性", "detail": "历史记录目录不存在"}]

    today_str = TODAY.isoformat()
    existing_reports = [f.name for f in history_dir.iterdir() if today_str in f.name]

    for rtype in REPORT_TYPES:
        if not any(rtype in f for f in existing_reports):
            alerts.append({"level": "info", "check": "完整性", "detail": f"今日未生成 {rtype}"})

    return alerts


def fix_score_zero(pool_name: str, data: dict, dry_run: bool = False) -> tuple[dict, list[dict]]:
    """自愈：score=0且入池>3天的标的，强制降级到边缘池"""
    fixes = []
    stocks = data.get("stocks", [])
    remaining = []
    demoted = []

    for stock in stocks:
        code = stock.get("股票代码", stock.get("代码", "?"))
        name = stock.get("股票名称", stock.get("名称", "?"))
        score = stock.get("评分", stock.get("score", stock.get("综合评分", stock.get("综合分", None))))
        entry_date = stock.get("纳入日期", "")

        should_demote = False
        if score is not None:
            try:
                score_val = float(score)
                if score_val == 0 and entry_date:
                    try:
                        days = (TODAY - datetime.datetime.strptime(entry_date, "%Y-%m-%d").date()).days
                        if days >= 3:
                            should_demote = True
                    except ValueError:
                        pass
            except (ValueError, TypeError):
                pass

        if should_demote:
            demoted.append(stock)
            fixes.append({
                "time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
                "type": "score=0降级",
                "target": f"{pool_name}/{code} {name}",
                "before": f"评分=0, 入池{days}天",
                "after": "降级到边缘池"
            })
        else:
            remaining.append(stock)

    if not dry_run and demoted:
        # 更新原池
        data["stocks"] = remaining
        data["统计"] = data.get("统计", {})
        data["统计"]["持仓数"] = len(remaining)
        data["统计"]["更新日期"] = NOW.strftime("%Y-%m-%d %H:%M:%S")

        # 写入边缘池
        edge_pool_file = POOLS_DIR / "边缘池.json"
        if edge_pool_file.exists():
            ok, edge_data, _ = check_json_file(str(edge_pool_file))
            if ok:
                edge_stocks = edge_data.get("stocks", [])
                for item in demoted:
                    item["评分"] = max(item.get("评分", 0), 0)
                    item["纳入日期"] = TODAY.isoformat()
                    edge_stocks.append(item)
                edge_data["stocks"] = edge_stocks
                edge_data["统计"] = edge_data.get("统计", {})
                edge_data["统计"]["持仓数"] = len(edge_stocks)
                edge_data["统计"]["更新日期"] = NOW.strftime("%Y-%m-%d %H:%M:%S")
                with open(edge_pool_file, "w", encoding="utf-8") as f:
                    json.dump(edge_data, f, ensure_ascii=False, indent=2)

        # 写回原池
        pool_file = POOLS_DIR / f"{pool_name}.json"
        with open(pool_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return data, fixes


def fix_duplicates(pool_name: str, data: dict, dry_run: bool = False) -> tuple[dict, list[dict]]:
    """自愈：去重，保留最新"""
    fixes = []
    stocks = data.get("stocks", [])
    seen = set()
    unique = []

    for stock in stocks:
        code = stock.get("股票代码", stock.get("代码", ""))
        if code in seen:
            name = stock.get("股票名称", stock.get("名称", "?"))
            fixes.append({
                "time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
                "type": "去重",
                "target": f"{pool_name}/{code} {name}",
                "before": "重复条目",
                "after": "已移除"
            })
            continue
        seen.add(code)
        unique.append(stock)

    if not dry_run and len(unique) < len(stocks):
        data["stocks"] = unique
        data["统计"] = data.get("统计", {})
        data["统计"]["持仓数"] = len(unique)
        pool_file = POOLS_DIR / f"{pool_name}.json"
        with open(pool_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return data, fixes


def log_fixes(fixes: list[dict]):
    """记录修复日志"""
    if not fixes:
        return
    with open(FIX_LOG, "a", encoding="utf-8") as f:
        for fix in fixes:
            f.write(json.dumps(fix, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="天枢数据质量扫描")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    parser.add_argument("--alert", action="store_true", help="告警输出")
    parser.add_argument("--fix", action="store_true", help="自动修复")
    parser.add_argument("--dry-run", action="store_true", help="预览修复")
    args = parser.parse_args()

    start = datetime.datetime.now()
    all_alerts = []
    all_fixes = []
    all_codes = {}
    dim_results = {}
    total_checks = 0
    passed = 0
    warnings = 0
    criticals = 0

    # 1. 五池扫描
    for pool_name in POOL_NAMES:
        pool_file = POOLS_DIR / f"{pool_name}.json"
        if not pool_file.exists():
            all_alerts.append({"level": "critical", "check": "完整性", "detail": f"{pool_name}: 文件不存在"})
            criticals += 1
            continue

        ok, data, err = check_json_file(str(pool_file))
        if not ok:
            all_alerts.append({"level": "critical", "check": "完整性", "detail": f"{pool_name}: {err}"})
            criticals += 1
            continue

        # 完整性
        integrity_alerts = check_pool_integrity(pool_name, data)
        all_alerts.extend(integrity_alerts)
        for a in integrity_alerts:
            if a["level"] == "critical": criticals += 1
            else: warnings += 1
        total_checks += 1

        # 准确性
        accuracy_alerts = check_score_accuracy(pool_name, data)
        all_alerts.extend(accuracy_alerts)
        for a in accuracy_alerts:
            if a["level"] == "critical": criticals += 1
            else: warnings += 1
        total_checks += 1

        # 重复
        dup_alerts = check_duplicates(pool_name, data, all_codes)
        all_alerts.extend(dup_alerts)
        for a in dup_alerts:
            if a["level"] == "critical": criticals += 1
            else: warnings += 1
        total_checks += 1

        # 及时性
        time_alerts = check_timeliness(pool_name, data)
        all_alerts.extend(time_alerts)
        for a in time_alerts:
            if a["level"] == "critical": criticals += 1
            else: warnings += 1
        total_checks += 1

        # 代码有效性
        code_alerts = check_code_validity(pool_name, data)
        all_alerts.extend(code_alerts)
        for a in code_alerts:
            if a["level"] == "critical": criticals += 1
            else: warnings += 1
        total_checks += 1

        # 自愈修复
        if args.fix or args.dry_run:
            data, score_fixes = fix_score_zero(pool_name, data, dry_run=args.dry_run)
            all_fixes.extend(score_fixes)
            data, dup_fixes = fix_duplicates(pool_name, data, dry_run=args.dry_run)
            all_fixes.extend(dup_fixes)

    # 2. 决策日志检查
    log_alerts = check_decision_log()
    all_alerts.extend(log_alerts)
    for a in log_alerts:
        if a["level"] == "critical": criticals += 1
        elif a["level"] == "warning": warnings += 1
    total_checks += 1

    # 3. 报告检查
    report_alerts = check_daily_reports()
    all_alerts.extend(report_alerts)
    for a in report_alerts:
        if a["level"] == "warning": warnings += 1
    total_checks += 1

    # 记录修复日志
    if all_fixes:
        log_fixes(all_fixes)

    # 计算通过数
    passed = total_checks - warnings - criticals

    # 输出
    result = {
        "timestamp": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "critical" if criticals > 0 else ("warning" if warnings > 0 else "healthy"),
        "exit_code": 2 if criticals > 0 else (1 if warnings > 0 else 0),
        "summary": {
            "total_checks": total_checks,
            "passed": passed,
            "warnings": warnings,
            "criticals": criticals,
            "fixed": len(all_fixes)
        },
        "alerts": all_alerts,
        "fixes": all_fixes
    }

    if args.verbose:
        print(f"📊 天枢数据质量扫描 | {result['timestamp']}")
        print(f"状态: {'🔴' if criticals>0 else '🟡' if warnings>0 else '✅'} {result['status'].upper()}")
        print(f"检查: {total_checks}项 | ✅ {passed} | ⚠️ {warnings} | 🔴 {criticals} | 🔧 {len(all_fixes)}")
        if all_alerts:
            print(f"\n告警清单:")
            for a in all_alerts:
                le = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}.get(a["level"], "❓")
                print(f"  {le} [{a['check']}] {a['detail']}")
        if all_fixes:
            print(f"\n修复记录:")
            for f in all_fixes:
                print(f"  🔧 [{f['type']}] {f['target']}: {f['before']} → {f['after']}")
    elif args.alert and all_alerts:
        le = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}.get
        for a in all_alerts:
            if a["level"] in ("critical", "warning"):
                print(f"{le(a['level'], '❓')} [{a['check']}] {a['detail']}")
    else:
        # JSON输出
        print(json.dumps(result, ensure_ascii=False, indent=2))

    # 保存结果
    result_file = DQ_DIR / f"dq_result_{TODAY.isoformat()}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    sys.exit(result["exit_code"])

if __name__ == "__main__":
    main()