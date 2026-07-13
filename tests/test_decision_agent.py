"""决策Agent核心函数单元测试"""
import sys, re
sys.path.insert(0, 'agents')
sys.path.insert(0, '.')

TOTAL = 0
PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global TOTAL, PASS, FAIL
    TOTAL += 1
    if condition:
        PASS += 1
    else:
        FAIL += 1
        suffix = f" | {detail}" if detail else ""
        print(f"  ❌ {name}{suffix}")


def test_filter_duplicate_empty():
    from decision_agent import DecisionAgent
    da = DecisionAgent()
    warning, names = da._filter_duplicate_recommendations([], {})
    assert warning == ""
    check("空列表→空警告", True)


def test_filter_duplicate_no_dup():
    from decision_agent import DecisionAgent
    da = DecisionAgent()
    stocks = [{"code": "000001", "name": "平安银行", "score": 80}]
    warning, names = da._filter_duplicate_recommendations(stocks, {})
    assert len(stocks) == 1
    check("无重复→保留所有", True)


def test_filter_duplicate_crash_safe():
    from decision_agent import DecisionAgent
    da = DecisionAgent()
    stocks = [{"code": "600519", "name": "贵州茅台", "score": 85}]
    try:
        warning, names = da._filter_duplicate_recommendations(stocks, {})
        check("重复检测不崩溃", True)
    except Exception as e:
        check("重复检测不崩溃", False, str(e))


def test_handle_expired_s_pool():
    from decision_agent import DecisionAgent
    da = DecisionAgent()
    try:
        da._handle_expired_s_pool()
        check("无过期标的→不崩溃", True)
    except Exception as e:
        check("无过期标的→不崩溃", False, str(e))


def test_scoring_patterns():
    patterns = [
        r'综合评分[：:\s]*\[?\*?\s*(\d+)',
        r'综合(?:分|评分)\s*[：:\s]*\*?\s*(\d+)',
        r'(?:评分|得分)[：:\s]*\*?\s*(\d+)\s*分',
        r'[（(]\s*(\d+)\s*分\s*[)）]',
        r'(\d+)\s*分[，,。\.\s]*(?:综合|四维|审查)',
        r'(?:综合|标的|审查)?评分[：:\s]*\*?\s*(\d+)(?:\s*分)?',
    ]
    cases = [("综合评分：85", 85), ("综合分 75", 75), ("评分：92分", 92),
             ("（85分）", 85), ("85分，综合评估", 85), ("标的评分: 78", 78), ("没有评分", 0)]
    for text, expected in cases:
        found = 0
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                found = int(m.group(1))
                break
        assert found == expected, f"'{text}' → {found}, 期望{expected}"
        check(f"评分提取 '{text[:15]}'", True)


def test_thresholds_constants():
    from thresholds import (DECISION_MIN_SCORE, SCORE_C_LEVEL,
                            ML_LOW_CONFIDENCE, ML_BLOCK_THRESHOLD,
                            SCORE_BASE_HIGH, SCORE_BASE_MED)
    assert DECISION_MIN_SCORE == 75
    assert SCORE_C_LEVEL == 55
    assert ML_LOW_CONFIDENCE == 45
    assert ML_BLOCK_THRESHOLD == 50
    assert SCORE_BASE_HIGH == 70
    assert SCORE_BASE_MED == 50
    check("阈值常量验证", True)


def test_gate_controller_yellow():
    from gate_controller import GateController
    stocks = [{"code": "000001", "score": 85}, {"code": "000002", "score": 65}]
    alerts = GateController.get_yellow_alerts(stocks)
    assert len(alerts) == 1
    assert alerts[0]["code"] == "000002"
    check("黄色预警区间[60-75)", True)
