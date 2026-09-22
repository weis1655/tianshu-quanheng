#!/usr/bin/env python3
"""
test_skeptic_parser_20260922.py — 09-22 4 次全降级回归测试

背景：2026-09-22 全天 4 次 LLM 调用（7465/10437/8644/4909 chars）全部解析失败，
降级为"规则化挑战"（LLM质疑审查不可用，规则模式：需人工复核该标的风险）。
根因：_parse_text_fallback 用 re.split(r'##?\\s*\\[?(\\d{6})\\]?\\s*', text) 期望
`## 002156` 或 `## [002156]` 格式，但 LLM 实际输出 markdown：
    ### ⚠️ 通富微电（002156）
    **判定**：challenge_required
    **摘要**：无
    | 维度 | 质疑内容 | 严重性 |
    |------|---------|--------|
    | 风险低估 | LLM质疑审查不可用... | 🟡 medium |

本测试验证：
1. _parse_text_fallback 能识别 09-22 实际 markdown 结构
2. 提取到每只股票的 code/name/overall_verdict/summary/challenges
3. 从表格里解析 severity（🔴 high / 🟡 medium / ⛔ veto / 🟢 low）
4. 表头 | 维度 | 质疑内容 | 严重性 | 与分隔线 |------| 被正确跳过
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))

from skeptic_agent import SkepticAgent  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ {name} | {detail}")
        FAIL += 1


def make_agent():
    """构造 SkepticAgent 实例，仅用于调用 _parse_text_fallback。"""
    sa = SkepticAgent.__new__(SkepticAgent)
    sa.agent_name = "SkepticAgent"
    return sa


# ---- 用例1: 09-22 实际报告（rule-fallback 后 markdown） ----
# 复制自 data/历史记录/2026-09-22_质疑审查报告.md 的核心区块
SAMPLE_0922 = """# 【质疑审查报告】2026-09-22 14:37

## 🔴 高风险股票

### 1. 通富微电（002156）
**摘要**：

**关键质疑**：
- **风险低估**（high）：【自动】通富微电(002156)报告含关键词「商誉」

## 📋 完整质疑详情

### ⚠️ 通富微电（002156）
**判定**：challenge_required
**摘要**：无

| 维度 | 质疑内容 | 严重性 |
|------|---------|--------|
| 风险低估 | LLM质疑审查不可用，规则模式：需人工复核该标的风险 | 🟡 medium |
| 风险低估 | 【自动】通富微电(002156)报告含关键词「商誉」 | 🔴 high |

---

### ⚠️ 江波龙（301308）
**判定**：challenge_required
**摘要**：无

| 维度 | 质疑内容 | 严重性 |
|------|---------|--------|
| 风险低估 | LLM质疑审查不可用，规则模式：需人工复核该标的风险 | 🟡 medium |

---

### ⚠️ 恒瑞医药（600276）
**判定**：challenge_required
**摘要**：无

| 维度 | 质疑内容 | 严重性 |
|------|---------|--------|
| 风险低估 | LLM质疑审查不可用，规则模式：需人工复核该标的风险 | 🟡 medium |
"""


def test_0922_actual_markdown():
    global PASS, FAIL
    print("\n[test_0922_actual_markdown] 09-22 实际报告 markdown 解析")
    sa = make_agent()
    challenges = sa._parse_text_fallback(SAMPLE_0922)
    check("解析出至少3只股票", len(challenges) >= 3, f"got {len(challenges)}")
    if not challenges:
        return

    codes = {c.get("code") for c in challenges}
    check("通富微电(002156)被识别", "002156" in codes, f"codes={codes}")
    check("江波龙(301308)被识别", "301308" in codes, f"codes={codes}")
    check("恒瑞医药(600276)被识别", "600276" in codes, f"codes={codes}")

    tfe = next((c for c in challenges if c.get("code") == "002156"), None)
    check("通富微电 name=通富微电", tfe and tfe.get("name") == "通富微电",
          f"got {tfe}")
    check("通富微电 verdict=challenge_required",
          tfe and tfe.get("overall_verdict") == "challenge_required",
          f"got {tfe.get('overall_verdict') if tfe else None}")
    check("通富微电 有2条挑战",
          tfe and len(tfe.get("challenges", [])) == 2,
          f"got {len(tfe.get('challenges', [])) if tfe else 0}")

    if tfe and len(tfe["challenges"]) >= 2:
        c0, c1 = tfe["challenges"][0], tfe["challenges"][1]
        check("第1条 severity=medium (🟡)",
              c0.get("severity") == "medium", f"got {c0}")
        check("第2条 severity=high (🔴)",
              c1.get("severity") == "high", f"got {c1}")
        check("表格行不被误当标题",
              all(c.get("dimension") == "风险低估" for c in tfe["challenges"]),
              f"got {[c.get('dimension') for c in tfe['challenges']]}")


# ---- 用例2: ✅ 通过 emoji + 无挑战表格 ----
SAMPLE_PASS = """### ✅ 兆易创新（603986）
**判定**：pass
**摘要**：整体通过，风险可控

| 维度 | 质疑内容 | 严重性 |
|------|---------|--------|
| 位置分析 | 估值偏高但不离谱 | 🟢 low |
"""


def test_pass_format():
    global PASS, FAIL
    print("\n[test_pass_format] ✅ pass 格式")
    sa = make_agent()
    out = sa._parse_text_fallback(SAMPLE_PASS)
    check("1条股票", len(out) == 1, f"got {len(out)}")
    if not out:
        return
    s = out[0]
    check("code=603986", s.get("code") == "603986")
    check("verdict=pass", s.get("overall_verdict") == "pass",
          f"got {s.get('overall_verdict')}")
    check("summary 非空", bool(s.get("summary", "")))
    check("low severity (🟢)",
          len(s.get("challenges", [])) == 1
          and s["challenges"][0].get("severity") == "low",
          f"got {s.get('challenges')}")


# ---- 用例3: veto severity 识别 ----
SAMPLE_VETO = """### ⚠️ 华谊兄弟（002594）
**判定**：challenge_required
**摘要**：财务造假嫌疑

| 维度 | 质疑内容 | 严重性 |
|------|---------|--------|
| 风险低估 | 财务造假嫌疑 | ⛔ veto |
"""


def test_veto_severity():
    global PASS, FAIL
    print("\n[test_veto_severity] ⛔ veto 识别")
    sa = make_agent()
    out = sa._parse_text_fallback(SAMPLE_VETO)
    check("1条股票", len(out) == 1)
    if not out:
        return
    c = out[0]["challenges"][0]
    check("severity=veto", c.get("severity") == "veto", f"got {c}")


# ---- 用例4: 中文冒号 vs 英文冒号 ----
SAMPLE_CN_COLON = """### ⚠️ 平安银行（000001）
**判定**：challenge_required
**摘要**：风险中等

| 维度 | 质疑内容 | 严重性 |
|------|---------|--------|
| 驱动逻辑 | 测试 | 🔴 high |
"""


def test_cn_colon():
    global PASS, FAIL
    print("\n[test_cn_colon] 中文冒号兼容性")
    sa = make_agent()
    out = sa._parse_text_fallback(SAMPLE_CN_COLON)
    check("verdict 解析",
          out and out[0].get("overall_verdict") == "challenge_required",
          f"got {out[0].get('overall_verdict') if out else None}")
    check("summary 解析",
          out and out[0].get("summary") == "风险中等",
          f"got {out[0].get('summary') if out else None}")


# ---- 用例5: 空输入不崩溃 ----
def test_empty():
    global PASS, FAIL
    print("\n[test_empty] 空输入")
    sa = make_agent()
    check("空字符串返回[]", sa._parse_text_fallback("") == [])
    check("无 markdown 返回[]", sa._parse_text_fallback("随便一段文字") == [])


if __name__ == "__main__":
    test_0922_actual_markdown()
    test_pass_format()
    test_veto_severity()
    test_cn_colon()
    test_empty()

    print(f"\n{'='*40}")
    print(f"总计: {PASS + FAIL}  通过: {PASS}  失败: {FAIL}")
    sys.exit(0 if FAIL == 0 else 1)
