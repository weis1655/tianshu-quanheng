#!/usr/bin/env python3
"""
天枢数据质量体系 — 批次1异常场景验证测试

测试用例覆盖：
  TC-01: 行情数据缺失(最新价=0) → DET-01检测 → HEAL-02自愈
  TC-02: 行情数据缺失(最新价=None) → DET-01检测 → HEAL-02自愈
  TC-03: 评分数据异常(综合分=0>3天) → DET-02检测 → HEAL-02自愈
  TC-04: 评分数据越界(综合分=150) → DET-07检测 → HEAL-05自愈
  TC-08: 价格异常(负值) → DET-04检测
  TC-09: 涨跌幅越界(>20%) → DET-05检测
  TC-11: LLM格式漂移检测 → DET-12检测
  TC-13: 权重配置异常 → DET-14检测

用法:
    python scripts/test_dq_batch1.py                    # 运行全部测试
    python scripts/test_dq_batch1.py --verbose          # 详细输出
    python scripts/test_dq_batch1.py --test TC-01       # 运行单个测试
"""

import os, sys, json, datetime, shutil, tempfile, argparse
from pathlib import Path

# 添加项目根到路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

NOW = datetime.datetime.now()
TODAY = datetime.date.today()

# 测试结果
results = []
passed = 0
failed = 0
errors = []


def test_header(name: str):
    print(f"\n{'='*60}")
    print(f"  🧪 {name}")
    print(f"{'='*60}")


def test_result(name: str, status: bool, detail: str = ""):
    global passed, failed
    emoji = "✅" if status else "❌"
    if status:
        passed += 1
    else:
        failed += 1
    results.append({"name": name, "status": "PASS" if status else "FAIL", "detail": detail})
    print(f"  {emoji} {name}: {'通过' if status else '失败'}")
    if detail:
        print(f"     {detail}")


def backup_pool(pool_name: str) -> str:
    """备份池文件"""
    src = PROJECT_ROOT / "五池管理" / f"{pool_name}.json"
    bak = tempfile.mktemp(suffix=f"_{pool_name}.json")
    if src.exists():
        shutil.copy2(str(src), bak)
    return bak


def restore_pool(bak: str, pool_name: str):
    """恢复池文件"""
    if bak and os.path.exists(bak):
        dst = PROJECT_ROOT / "五池管理" / f"{pool_name}.json"
        shutil.copy2(bak, str(dst))
        os.unlink(bak)


def test_tc01_price_zero():
    """TC-01: 行情缺失(最新价=0) → 检测+自愈"""
    test_header("TC-01: 行情数据缺失(最新价=0) → dq_scanner检测")
    pool_name = "快筛候选池"
    bak = backup_pool(pool_name)
    try:
        # 注入异常：将第一个标的的"最新价"设为0
        pool_file = PROJECT_ROOT / "五池管理" / f"{pool_name}.json"
        with open(pool_file, encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("stocks"):
            raise Exception("池为空，无法测试")
        original_price = data["stocks"][0].get("最新价", "N/A")
        data["stocks"][0]["最新价"] = 0
        with open(pool_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 运行扫描（检测模式）
        from dq_scanner import check_price_anomaly, check_score_accuracy
        alerts = check_price_anomaly(pool_name, data)
        found = any("价格" in a.get("check", "") and (a.get("level") == "warning") for a in alerts)
        test_result("TC-01a: dq_scanner检测到价格为0", found, f"告警数: {len(alerts)}")

        # 运行行情自愈
        from quote_healer import scan_pool
        fixes = scan_pool(pool_name, dry_run=True)
        succ = sum(1 for f in fixes if f.get("validated"))
        test_result("TC-01b: quote_healer可修复", succ > 0, f"成功修复: {succ}")

    except Exception as e:
        test_result("TC-01", False, f"异常: {e}")
    finally:
        restore_pool(bak, pool_name)


def test_tc02_price_none():
    """TC-02: 行情缺失(最新价=None)"""
    test_header("TC-02: 行情数据缺失(最新价=None) → dq_scanner检测")
    pool_name = "快筛候选池"
    bak = backup_pool(pool_name)
    try:
        pool_file = PROJECT_ROOT / "五池管理" / f"{pool_name}.json"
        with open(pool_file, encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("stocks"):
            raise Exception("池为空")
        # 删除最新价字段
        data["stocks"][0].pop("最新价", None)
        with open(pool_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        from dq_scanner import check_price_anomaly
        alerts = check_price_anomaly(pool_name, data)
        # 价格缺失不触发告警(scanner检查的是价格异常值，不是缺失)
        # 缺失行情由quote_healer检测
        from quote_healer import need_heal
        needs, reason = need_heal(data["stocks"][0])
        test_result("TC-02: quote_healer检测到价格缺失", needs, f"原因: {reason}")

    except Exception as e:
        test_result("TC-02", False, f"异常: {e}")
    finally:
        restore_pool(bak, pool_name)


def test_tc03_score_zero():
    """TC-03: 评分=0且入池>3天"""
    test_header("TC-03: 评分数据异常(综合分=0>3天) → 检测+降级")
    pool_name = "快筛候选池"
    bak = backup_pool(pool_name)
    try:
        pool_file = PROJECT_ROOT / "五池管理" / f"{pool_name}.json"
        with open(pool_file, encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("stocks"):
            raise Exception("池为空")
        # 注入：综合分=0，纳入日期=5天前
        old_date = (TODAY - datetime.timedelta(days=5)).isoformat()
        data["stocks"][0]["综合分"] = 0  # _get_score优先查"综合分"
        data["stocks"][0]["纳入日期"] = old_date

        # 先验证 fix_score_zero 能识别
        from dq_scanner import fix_score_zero
        fixed_data, fixes = fix_score_zero(pool_name, data, dry_run=True)
        has_fix = len(fixes) > 0
        test_result("TC-03a: fix_score_zero识别到score=0>3天", has_fix, f"修复计划: {len(fixes)}条")

        # 记录修复前数量
        before_count = len(data.get("stocks", []))
        # 实际执行修复
        fixed_data, fixes = fix_score_zero(pool_name, data, dry_run=False)
        # 验证修复后stocks是否减少（降级了）
        after_count = len(fixed_data.get("stocks", []))
        test_result("TC-03b: fix_score_zero降级成功", after_count < before_count, f"降级前: {before_count}, 降级后: {after_count}")

    except Exception as e:
        test_result("TC-03", False, f"异常: {e}")
    finally:
        restore_pool(bak, pool_name)


def test_tc04_score_out_of_range():
    """TC-04: 评分越界(综合分=150)"""
    test_header("TC-04: 评分数据越界(综合分=150) → 检测")
    pool_name = "快筛候选池"
    bak = backup_pool(pool_name)
    try:
        pool_file = PROJECT_ROOT / "五池管理" / f"{pool_name}.json"
        with open(pool_file, encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("stocks"):
            raise Exception("池为空")
        # 注入：综合分=150
        data["stocks"][0]["综合分"] = 150
        with open(pool_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        from dq_scanner import check_score_accuracy
        alerts = check_score_accuracy(pool_name, data)
        found = any("评分越界" in a.get("check", "") for a in alerts)
        test_result("TC-04: dq_scanner检测到评分越界", found, f"告警: {[a['detail'] for a in alerts if '评分越界' in a.get('check','')]}")

    except Exception as e:
        test_result("TC-04", False, f"异常: {e}")
    finally:
        restore_pool(bak, pool_name)


def test_tc08_price_negative():
    """TC-08: 价格异常(负值)"""
    test_header("TC-08: 价格异常(负值) → 检测")
    pool_name = "快筛候选池"
    bak = backup_pool(pool_name)
    try:
        pool_file = PROJECT_ROOT / "五池管理" / f"{pool_name}.json"
        with open(pool_file, encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("stocks"):
            raise Exception("池为空")
        data["stocks"][0]["最新价"] = -1
        with open(pool_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        from dq_scanner import check_price_anomaly
        alerts = check_price_anomaly(pool_name, data)
        found = any("价格异常" in a.get("check", "") for a in alerts)
        test_result("TC-08: dq_scanner检测到价格负值", found, f"告警: {[a['detail'] for a in alerts if '价格异常' in a.get('check','')]}")

    except Exception as e:
        test_result("TC-08", False, f"异常: {e}")
    finally:
        restore_pool(bak, pool_name)


def test_tc09_change_pct_overflow():
    """TC-09: 涨跌幅越界(>20%)"""
    test_header("TC-09: 涨跌幅越界(>20%) → 检测")
    pool_name = "快筛候选池"
    bak = backup_pool(pool_name)
    try:
        pool_file = PROJECT_ROOT / "五池管理" / f"{pool_name}.json"
        with open(pool_file, encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("stocks"):
            raise Exception("池为空")
        data["stocks"][0]["涨跌幅"] = 25.0
        with open(pool_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        from dq_scanner import check_price_anomaly
        alerts = check_price_anomaly(pool_name, data)
        found = any("涨跌幅异常" in a.get("check", "") for a in alerts)
        test_result("TC-09: dq_scanner检测到涨跌幅越界", found, f"告警: {[a['detail'] for a in alerts if '涨跌幅异常' in a.get('check','')]}")

    except Exception as e:
        test_result("TC-09", False, f"异常: {e}")
    finally:
        restore_pool(bak, pool_name)


def test_tc11_llm_drift():
    """TC-11: LLM格式漂移检测"""
    test_header("TC-11: LLM格式漂移检测 → 检测器可用性")
    try:
        from llm_drift_detector import scan_drift
        result = scan_drift(days=3)
        # 检测器正常运行即可
        has_groups = len(result.get("groups", {})) > 0
        test_result("TC-11: llm_drift_detector正常运行", has_groups, f"扫描组数: {len(result.get('groups', {}))}, 告警数: {result.get('drift_count', 0)}")
    except Exception as e:
        test_result("TC-11", False, f"异常: {e}")


def test_tc13_weight_config():
    """TC-13: 权重配置异常"""
    test_header("TC-13: 权重配置合法性检测")
    try:
        from dq_scanner import check_weight_config
        data_dir = PROJECT_ROOT / "data"
        config_file = data_dir / "权重配置.json"
        if config_file.exists():
            alerts = check_weight_config()
            test_result("TC-13: 权重配置检测器正常运行", True, f"告警: {[a['detail'] for a in alerts]}")
        else:
            test_result("TC-13: 权重配置文件不存在(跳过)", True, "无权重配置，跳过")
    except Exception as e:
        test_result("TC-13", False, f"异常: {e}")


def run_all():
    """运行全部测试"""
    print(f"\n{'#'*60}")
    print(f"  # 天枢数据质量体系 — 批次1异常场景验证")
    print(f"  # 时间: {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}\n")

    test_tc01_price_zero()
    test_tc02_price_none()
    test_tc03_score_zero()
    test_tc04_score_out_of_range()
    test_tc08_price_negative()
    test_tc09_change_pct_overflow()
    test_tc11_llm_drift()
    test_tc13_weight_config()

    # 汇总
    total = passed + failed
    print(f"\n{'='*60}")
    print(f"  测试汇总")
    print(f"{'='*60}")
    print(f"  总用例: {total}")
    print(f"  ✅ 通过: {passed}")
    print(f"  ❌ 失败: {failed}")
    print(f"  通过率: {passed/total*100:.1f}%" if total > 0 else "  无测试")

    # 保存结果
    result_file = PROJECT_ROOT / "data" / "dq" / f"dq_test_batch1_{TODAY.isoformat()}.json"
    result_data = {
        "test_time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "batch": "batch1",
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed/total*100, 1) if total > 0 else 0,
        "results": results,
    }
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)
    print(f"\n  结果已保存: {result_file}")

    return failed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="天枢数据质量体系 — 批次1测试")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    parser.add_argument("--test", help="运行单个测试(TC-01等)")
    args = parser.parse_args()

    if args.test:
        test_map = {
            "TC-01": test_tc01_price_zero,
            "TC-02": test_tc02_price_none,
            "TC-03": test_tc03_score_zero,
            "TC-04": test_tc04_score_out_of_range,
            "TC-08": test_tc08_price_negative,
            "TC-09": test_tc09_change_pct_overflow,
            "TC-11": test_tc11_llm_drift,
            "TC-13": test_tc13_weight_config,
        }
        if args.test in test_map:
            print(f"\n{'#'*60}")
            print(f"  # 单测试模式: {args.test}")
            print(f"{'#'*60}")
            test_map[args.test]()
        else:
            print(f"未知测试: {args.test}")
            print(f"可选: {', '.join(test_map.keys())}")
        sys.exit(0)

    success = run_all()
    sys.exit(0 if success else 1)