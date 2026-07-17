#!/usr/bin/env python3
"""
天枢权衡 · 合规规则场景化测试

覆盖15个测试场景（TC-001 ~ TC-015）
验证事前拦截、事中告警的正确性

用法：
  python scripts/test_compliance.py        # 全量测试
  python scripts/test_compliance.py --list  # 列出用例
"""

import sys, json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "agents"))
from path_config import ensure_agent_paths; ensure_agent_paths()
from compliance_manager import ComplianceChecker, get_checker, get_logger


def reset_checker():
    """重置合规检查器状态（测试前调用）"""
    import compliance_manager
    compliance_manager._checker_instance = None
    return ComplianceChecker()


def test_tc001_st_block():
    """TC-001: 推荐ST标的 → 必须拦截"""
    c = reset_checker()
    passed, reasons = c.check_all("000001", "ST华药", 100000, 1_000_000, 10, 10, is_st=True)
    assert not passed, "ST标的应被拦截"
    assert any("ST" in r for r in reasons), "拦截原因应包含ST"
    print("  ✅ TC-001: ST标的拦截")


def test_tc002_delist_block():
    """TC-002: 退市整理期标的 → 必须拦截"""
    c = reset_checker()
    passed, reasons = c.check_all("400001", "退市整理A", 100000, 1_000_000, 10, 10)
    assert not passed, "退市标的应被拦截"
    print("  ✅ TC-002: 退市标的拦截")


def test_tc003_price_upper_limit():
    """TC-003: 买入价超过涨停价 → 拦截"""
    c = reset_checker()
    passed, reasons = c.check_all("600519", "贵州茅台", 100000, 1_000_000,
                                  100.0, 115.0)  # 前收100, 买入价115 > 涨停110
    assert not passed, "超涨停价申报应被拦截"
    print("  ✅ TC-003: 超涨停价拦截")


def test_tc004_price_lower_limit():
    """TC-004: 卖出价低于跌停价 → 拦截"""
    c = reset_checker()
    passed, reasons = c.check_all("600519", "贵州茅台", 100000, 1_000_000,
                                  100.0, 85.0)  # 前收100, 卖出价85 < 跌停90
    assert not passed, "低于跌停价申报应被拦截"
    print("  ✅ TC-004: 低于跌停价拦截")


def test_tc005_t_plus_1():
    """TC-005: 当日买入当日卖出 → 拦截"""
    c = reset_checker()
    passed, reasons = c.check_all("600519", "贵州茅台", 100000, 1_000_000,
                                  100.0, 101.0, buy_date="2026-07-17", today="2026-07-17")
    assert not passed, "T+1应拦截当日卖出"
    print("  ✅ TC-005: T+1拦截")


def test_tc006_position_oversize():
    """TC-006: 单票仓位超10% → 拦截"""
    c = reset_checker()
    passed, reasons = c.check_all("600519", "贵州茅台", 900000, 1_000_000,
                                  100.0, 100.0, current_position_pct=0)
    assert not passed, "单票>10%仓位应拦截"
    print("  ✅ TC-006: 仓位超限拦截")


def test_tc007_reporting_line():
    """TC-007: 流通股占比≥5%举牌线 → 拦截"""
    c = reset_checker()
    passed, reasons = c.check_all("600519", "贵州茅台", 500000, 1_000_000,
                                  100.0, 100.0, current_float_pct=4.5)
    assert not passed, "≥5%举牌线应拦截"
    print("  ✅ TC-007: 举牌线拦截")


def test_tc008_insider_info():
    """TC-008: 内幕信息标的 → 拦截"""
    c = reset_checker()
    from compliance_manager import INSIDER_STOCKS
    INSIDER_STOCKS.add("600519")
    passed, reasons = c.check_all("600519", "贵州茅台", 100000, 1_000_000, 100.0, 100.0)
    assert not passed, "内幕信息标的应拦截"
    INSIDER_STOCKS.discard("600519")
    print("  ✅ TC-008: 内幕信息拦截")


def test_tc009_blacklist():
    """TC-009: 黑名单标的 → 拦截"""
    c = reset_checker()
    from compliance_manager import BLACKLIST_STOCKS
    BLACKLIST_STOCKS.add("600519")
    passed, reasons = c.check_all("600519", "贵州茅台", 100000, 1_000_000, 100.0, 100.0)
    assert not passed, "黑名单标的应拦截"
    BLACKLIST_STOCKS.discard("600519")
    print("  ✅ TC-009: 黑名单拦截")


def test_tc010_whitelist():
    """TC-010: 白名单外标的 → 拦截"""
    c = reset_checker()
    passed, reasons = c.check_all("000001", "平安银行", 100000, 1_000_000,
                                  100.0, 100.0, whitelist={"600519", "000002"})
    assert not passed, "白名单外标的应拦截"
    print("  ✅ TC-010: 白名单外拦截")


def test_tc011_daily_order_limit():
    """TC-011: 单日报单超50次 → 拦截"""
    c = reset_checker()
    for _ in range(50):
        c.record_order()
    passed, reasons = c.check_order_frequency()
    assert not passed, "日报单≥50次应拦截"
    print("  ✅ TC-011: 日报单频率拦截")


def test_tc012_minute_order_limit():
    """TC-012: 每分钟报单超5次 → 拦截"""
    c = reset_checker()
    for _ in range(5):
        c.record_order()
    passed, reasons = c.check_order_frequency()
    assert not passed, "分钟报单≥5次应拦截"
    print("  ✅ TC-012: 分钟报单频率拦截")


def test_tc013_cancel_ratio():
    """TC-013: 撤单比例超50% → 拦截"""
    c = reset_checker()
    # 6次报单 + 4次撤单 = 撤单比例 4/10 = 40% < 50%, OK
    c.record_order()
    c.record_order()
    c.record_order(is_cancel=True)
    c.record_order()
    # 当前: 4单 + 1撤 = 撤单率25%, OK
    c.record_order(is_cancel=True)
    c.record_order(is_cancel=True)
    c.record_order(is_cancel=True)  # 4单+4撤=8, 撤单率50% → 边界
    passed, reasons = c.check_order_frequency()
    assert not passed, f"撤单≥50%应拦截: {reasons}"
    print("  ✅ TC-013: 撤单比例拦截")


def test_tc015_normal_trade():
    """TC-015: 正常标的正常交易 → 放行"""
    c = reset_checker()
    passed, reasons = c.check_all("600519", "贵州茅台", 10000, 1_000_000,
                                  100.0, 101.0, buy_date="2026-07-16", today="2026-07-17",
                                  whitelist={"600519"})
    assert passed, f"正常交易应放行: {reasons}"
    print("  ✅ TC-015: 正常交易放行")


def test_compliance_log():
    """验证合规日志文件存在且格式正确"""
    c = reset_checker()
    c.check_all("000001", "ST华药", 100000, 1_000_000, 10, 10, is_st=True)
    log = c.logger
    today_log = log.get_today_log()
    assert len(today_log) > 0, "合规日志应有记录"
    assert today_log[0]["rule"] == "C-007", "日志应标记规则ID"
    assert today_log[0]["action"] == "block", "日志应标记拦截动作"
    print("  ✅ 合规日志验证")


def main():
    tests = [
        ("TC-001", "ST标的拦截", test_tc001_st_block),
        ("TC-002", "退市标的拦截", test_tc002_delist_block),
        ("TC-003", "超涨停价拦截", test_tc003_price_upper_limit),
        ("TC-004", "低于跌停价拦截", test_tc004_price_lower_limit),
        ("TC-005", "T+1拦截", test_tc005_t_plus_1),
        ("TC-006", "仓位超限拦截", test_tc006_position_oversize),
        ("TC-007", "举牌线拦截", test_tc007_reporting_line),
        ("TC-008", "内幕信息拦截", test_tc008_insider_info),
        ("TC-009", "黑名单拦截", test_tc009_blacklist),
        ("TC-010", "白名单外拦截", test_tc010_whitelist),
        ("TC-011", "日报单频率拦截", test_tc011_daily_order_limit),
        ("TC-012", "分钟报单频率拦截", test_tc012_minute_order_limit),
        ("TC-013", "撤单比例拦截", test_tc013_cancel_ratio),
        ("TC-015", "正常交易放行", test_tc015_normal_trade),
        ("CL-001", "合规日志验证", test_compliance_log),
    ]

    import argparse
    parser = argparse.ArgumentParser(description="合规规则场景化测试")
    parser.add_argument("--list", action="store_true", help="列出测试用例")
    args = parser.parse_args()

    if args.list:
        print(f"\n{'场景ID':<10} {'描述':<20}")
        print("-" * 30)
        for tid, desc, _ in tests:
            print(f"{tid:<10} {desc:<20}")
        return

    print(f"🏛️ 天枢合规规则测试 ({len(tests)}个场景)")
    print("=" * 40)
    passed = 0
    failed = 0
    for tid, desc, fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {tid}: {desc} — {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {tid}: {desc} — 异常: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"结果: ✅ {passed}/{len(tests)} | ❌ {failed}/{len(tests)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())