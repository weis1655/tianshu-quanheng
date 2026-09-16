"""
评分溯源模块测试 — score_source.py + review_agent._extract_confidence

锁定 2026-09-16 兆易创新 84/45 评分串味事故的四条防线。

隔离说明：所有池写操作都在 tempfile 目录，不触碰真实 五池管理/。
（见 tianshu-subsystem-development 陷阱 7.8）
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

import score_source as ss  # noqa: E402
from review_agent import ReviewAgent  # noqa: E402
from decision_utils import extract_scores  # noqa: E402

PASS, FAIL = 0, 0
RESULTS = []
TODAY = "2026-09-16"


def check(case_id, name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        RESULTS.append((case_id, name, "✅", ""))
    else:
        FAIL += 1
        RESULTS.append((case_id, name, "❌", detail))


def make_pool(tmp: Path, stocks: list) -> None:
    """构造隔离的重点观察池目录。"""
    (tmp / "五池管理").mkdir(parents=True, exist_ok=True)
    (tmp / "五池管理" / "重点观察池.json").write_text(
        json.dumps({"stocks": stocks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ══════════════════════════════════════════════════════════════
# 回归锚点：2026-09-16 真实事故形态
# ══════════════════════════════════════════════════════════════
REVIEW_0916 = """# 【审查报告】2026-09-16

━━━━━━━━━━━━━━━━

## 601398 工商银行

### 四维审查结果
| 维度 | 评分(0-100) | 说明 |
|------|-------------|------|
| 驱动验证 | 50 | 无明确催化剂 |
| 位置分析 | 55 | PE 7.74倍 |
| 量能判断 | 30 | 换手0.08%极低 |
| 风险扫描 | 85 | 无ST/退市风险 |
| **综合评分** | **45** | **安全但缺乏交易价值** |

### 流转方向
→ 淘汰 → 移出候选池

---

## 五池更新
审查执行时间：07:12

## 📋 重点观察池最新评估
| 股票 | 综合分 | 信心度 | 现价 | 涨跌 | 换手 | 量比 | 核心逻辑 |
|------|--------|--------|------|------|------|------|----------|
| 兆易创新(603986) | 84 | ，逻辑通顺，强烈建议关注 | 363.11 | +1.75% | 4.37% | 1.07 | 半导体国产替代 |
"""

DECISION_0916 = """# 【决策报告】2026-09-16

## 🚫 今日决策：空仓等待

| 评分<60分标的 | 兆易创新(45分)、工商银行(45分) |

### 兆易创新（603986）— 全部采纳
**结论**：兆易创新（45分）和工商银行（45分）均低于60分，不输出执行方案。
"""


# ══════════════════════════════════════════════════════════════
# 1. build_authoritative_scores — 权威分溯源
# ══════════════════════════════════════════════════════════════
def test_authoritative():
    tmp = Path(tempfile.mkdtemp(prefix="score_src_"))
    try:
        # 池内兆易=84（9-15 纳入，非当日审查）
        make_pool(tmp, [{
            "代码": "603986", "名称": "兆易创新", "综合分": 84,
            "信心度": "，逻辑通顺，强烈建议关注",
            "纳入日期": "2026-09-15",
        }])

        auth = ss.build_authoritative_scores(REVIEW_0916, tmp / "五池管理", TODAY)
        check("AS-01", "审查当日标的分=45 且来源 review_today",
              auth.get("601398", {}).get("score") == 45
              and auth["601398"]["source"] == "review_today")
        check("AS-02", "审查当日标的不算 stale",
              auth["601398"]["stale"] is False and auth["601398"]["review_date"] == TODAY)
        check("AS-03", "兆易分=84 来自池内缓存",
              auth.get("603986", {}).get("score") == 84
              and auth["603986"]["source"] == "pool_cache")
        check("AS-04", "兆易标 stale=True（非当日审查）",
              auth["603986"]["stale"] is True and auth["603986"]["review_date"] == "2026-09-15")
        check("AS-05", "兆易名称从池取到", auth["603986"]["name"] == "兆易创新")
        check("AS-06", "共识别 2 只标的", len(auth) == 2, f"got {len(auth)}")

        # 当日审查分 vs 池缓存分冲突时，以审查分为准
        make_pool(tmp, [{"代码": "601398", "名称": "工商银行", "综合分": 99,
                         "纳入日期": "2026-09-16"}])
        auth2 = ss.build_authoritative_scores(REVIEW_0916, tmp / "五池管理", TODAY)
        check("AS-07", "审查分优先于池缓存分（45 而非 99）",
              auth2["601398"]["score"] == 45 and auth2["601398"]["source"] == "review_today",
              f"got {auth2['601398']}")

        # 池空
        make_pool(tmp, [])
        auth3 = ss.build_authoritative_scores(REVIEW_0916, tmp / "五池管理", TODAY)
        check("AS-08", "池空时仅保留审查标的", set(auth3) == {"601398"})

        # 审查报告为空
        auth4 = ss.build_authoritative_scores("", tmp / "五池管理", TODAY)
        check("AS-09", "审查报告为空时返回空权威分", auth4 == {})

        # 空池目录（文件不存在）
        tmp2 = Path(tempfile.mkdtemp(prefix="score_src_"))
        auth5 = ss.build_authoritative_scores(REVIEW_0916, tmp2, TODAY)
        check("AS-10", "池文件缺失不抛异常", auth5.get("601398", {}).get("score") == 45)
        shutil.rmtree(tmp2)

        # 非法池数据（缺代码/代码非6位/分数非数字）
        make_pool(tmp, [
            {"名称": "无代码"},
            {"代码": "abc", "名称": "代码非法", "综合分": 50},
            {"代码": "600000", "名称": "分非数字", "综合分": "N/A", "纳入日期": TODAY},
            {"代码": "600001", "名称": "分缺失", "纳入日期": "2026-09-10"},
        ])
        auth6 = ss.build_authoritative_scores(REVIEW_0916, tmp / "五池管理", TODAY)
        check("AS-11", "非法池条目被跳过（无代码/非6位）",
              all(c in auth6 for c in ("600000", "600001")))
        check("AS-12", "分数非数字→None 不崩溃", auth6["600000"]["score"] is None)
        check("AS-13", "分数缺失→None 且标 stale",
              auth6["600001"]["score"] is None and auth6["600001"]["stale"] is True)
        check("AS-14", "当日纳入的池标的 stale=False",
              auth6["600000"]["stale"] is False)

        # 边界：分数 0 / 100
        make_pool(tmp, [
            {"代码": "600100", "名称": "边界低", "综合分": 0, "纳入日期": "2026-09-10"},
            {"代码": "600101", "名称": "边界高", "综合分": 100, "纳入日期": "2026-09-10"},
            {"代码": "600102", "名称": "越界", "综合分": 150, "纳入日期": "2026-09-10"},
        ])
        auth7 = ss.build_authoritative_scores(REVIEW_0916, tmp / "五池管理", TODAY)
        check("AS-15", "分数 0 被保留（非 None）", auth7["600100"]["score"] == 0)
        check("AS-16", "分数 100 被保留", auth7["600101"]["score"] == 100)
        check("AS-17", "分数越界裁剪到 100", auth7["600102"]["score"] == 100)
    finally:
        shutil.rmtree(tmp)


# ══════════════════════════════════════════════════════════════
# 2. trace_decision_scores — 决策报告分数溯源（事故核心拦截点）
# ══════════════════════════════════════════════════════════════
def test_trace():
    tmp = Path(tempfile.mkdtemp(prefix="score_src_"))
    try:
        make_pool(tmp, [{"代码": "603986", "名称": "兆易创新", "综合分": 84,
                         "纳入日期": "2026-09-15"}])
        auth = ss.build_authoritative_scores(REVIEW_0916, tmp / "五池管理", TODAY)
        issues = ss.trace_decision_scores(DECISION_0916, auth)

        by_code = {i["code"]: i for i in issues}
        check("TS-01", "共抓 2 条分数声明", len(issues) == 2, f"got {len(issues)}")
        check("TS-02", "工商银行声明45=权威45 → valid",
              by_code.get("601398", {}).get("valid") is True)
        check("TS-03", "兆易声明45≠权威84 → 被拦截",
              by_code.get("603986", {}).get("valid") is False,
              f"got {by_code.get('603986')}")
        check("TS-04", "兆易问题描述含缓存分提示",
              "缓存分" in by_code.get("603986", {}).get("issue", ""))
        check("TS-05", "兆易声明分=45（未被 84 覆盖）",
              by_code.get("603986", {}).get("declared") == 45)
        check("TS-06", "兆易权威分=84",
              by_code.get("603986", {}).get("authoritative") == 84)

        # 形式A：`兆易创新（603986）... 45分`（code 紧邻名称）
        auth_a = {"603986": {"code": "603986", "name": "兆易创新", "score": 84,
                             "source": "review_today", "review_date": TODAY, "stale": False}}
        r = ss.trace_decision_scores("兆易创新（603986）当前综合评分为 45分", auth_a)
        check("TS-07", "形式A（名称+代码+分数）可识别",
              r and r[0]["code"] == "603986" and r[0]["declared"] == 45 and r[0]["valid"] is False,
              f"got {r}")

        # 权威分与声明一致 → valid
        r2 = ss.trace_decision_scores("兆易创新（603986）综合评分 84分", auth_a)
        check("TS-08", "声明=权威 → valid", r2 and r2[0]["valid"] is True, f"got {r2}")

        # 权威来源完全没有该标的 → 标记来源不明
        r3 = ss.trace_decision_scores("兆易创新（603986）45分", {})
        check("TS-09", "权威源无此标的 → 判为来源不明",
              r3 and r3[0]["valid"] is False and "来源不明" in r3[0]["issue"],
              f"got {r3}")

        # 空报告
        check("TS-10", "空报告返回空列表",
              ss.trace_decision_scores("", auth) == [])

        # 无分数声明的报告
        check("TS-11", "无分数声明返回空列表",
              ss.trace_decision_scores("今日空仓等待", auth) == [])

        # 权威分 None（解析失败）
        auth_none = {"603986": {"code": "603986", "name": "兆易创新", "score": None,
                                "source": "review_today", "review_date": TODAY, "stale": False}}
        r4 = ss.trace_decision_scores("兆易创新（603986）45分", auth_none)
        check("TS-12", "权威分缺失 → 无法核对",
              r4 and r4[0]["valid"] is False and "缺失" in r4[0]["issue"],
              f"got {r4}")

        # 同名代码去重（seen 机制）
        dup = ss.trace_decision_scores(
            "兆易创新（603986）45分\n兆易创新（603986）45分", auth_a)
        check("TS-13", "同一 code 只上报一次", len(dup) == 1, f"got {len(dup)}")
    finally:
        shutil.rmtree(tmp)


# ══════════════════════════════════════════════════════════════
# 3. clean_confidence — 脏值清洗
# ══════════════════════════════════════════════════════════════
def test_clean_confidence():
    cases = [
        ("CF-01", None, 84, "高", True, "None→按分数推导"),
        ("CF-02", "", 84, "高", True, "空串→推导"),
        ("CF-03", "  ", 84, "高", True, "纯空白→推导"),
        ("CF-04", "，逻辑通顺，强烈建议关注", 84, "高", True, "标点开头（真实事故）→推导"),
        ("CF-05", "高", 84, "高", False, "正常值保留"),
        ("CF-06", "中高，逻辑通顺", 75, "中高，逻辑通顺", False, "中高保留"),
        ("CF-07", "低，风险高", 45, "低，风险高", False, "低保留"),
        ("CF-08", "中", 62, "中", False, "边界62→中"),
        ("CF-09", "中", 74, "中", False, "边界74→中"),
        ("CF-10", "", 45, "低", True, "45分→低"),
        ("CF-11", "", 75, "高", True, "75分→高"),
        ("CF-12", "", 60, "中", True, "60分→中"),
        ("CF-13", "", None, "中", True, "无分数→中"),
        ("CF-14", "", "N/A", "中", True, "分数非数字→中"),
        ("CF-15", "。", 84, "高", True, "中文句号开头→推导"),
        ("CF-16", "!", 84, "高", True, "半角叹号开头→推导"),
        ("CF-17", "a", 84, "高", True, "单字符→太短"),
        ("CF-18", "中高", 60, "中高", False, "中高长度2保留"),
    ]
    for cid, val, score, want_clean, want_dirty, note in cases:
        cleaned, dirty = ss.clean_confidence(val, score)
        check(f"{cid}-{note}", f"clean_confidence({val!r}, {score!r})",
              cleaned == want_clean and dirty == want_dirty,
              f"got ({cleaned!r}, {dirty}) want ({want_clean!r}, {want_dirty})")


# ══════════════════════════════════════════════════════════════
# 4. _extract_confidence — 审查报告信心度解析（「高」字丢失修复）
# ══════════════════════════════════════════════════════════════
def test_extract_confidence():
    f = ReviewAgent._extract_confidence
    cases = [
        ("EC-01", "| **综合评分** | **84** | **高信心度，逻辑通顺，强烈建议关注** |",
         84, "高，逻辑通顺，强烈建议关注", "真实事故形态：恢复「高」字"),
        ("EC-02", "| 综合评分 | 75 | 信心度：中高，逻辑通顺 |", 75, "中高，逻辑通顺",
         "冒号式中高"),
        ("EC-03", "| 综合评分 | 80 | 信心度：高 |", 80, "高", "冒号式高"),
        ("EC-04", "| **综合评分** | **70** | 信心度：中 |", 70, "中", "冒号式中"),
        ("EC-05", "| 综合评分 | 45 | 信心度：低 |", 45, "低", "冒号式低"),
        ("EC-06", "| 综合评分 | 80 | 逻辑通顺 |", 80, "高",
         "无信心度字段→按分数推导"),
        ("EC-07", "", 45, "低", "空块→推导"),
        ("EC-08", "| **综合评分** | **62** | **中，一般** |", 62, "中", "无前缀短值"),
        ("EC-09", "| 综合评分 | 68 | 信心度：中低，谨慎 |", 68, "中低，谨慎", "中低不被截成中"),
        ("EC-10", "| **综合评分** | **78** | **中信心度** |", 78, "中", "仅级别词无主体"),
    ]
    for cid, block, score, want, note in cases:
        got = f(block, score)
        check(cid, f"{note}: {block[:34]!r}", got == want, f"got {got!r} want {want!r}")


# ══════════════════════════════════════════════════════════════
# 5. 语义边界穿越回归（陷阱 7.6：禁止跨实体取分）
# ══════════════════════════════════════════════════════════════
def test_no_cross_entity():
    """两个表格并存时，不能把 A 表的分数抓成 B 表的字段。"""
    tmp = Path(tempfile.mkdtemp(prefix="score_src_"))
    try:
        report = """# 审查报告

## 600028 中国石化
| **综合评分** | **42** | **中，一般** |

## 📋 重点观察池最新评估
| 股票 | 综合分 | 信心度 | 现价 |
|------|--------|--------|------|
| 600141 兴发集团 | 31 | 中 | 31.50 |
| 002312 川发龙蟒 | 9 | 低 | 9.55 |
"""
        auth = ss.build_authoritative_scores(report, tmp / "五池管理", TODAY)
        check("CE-01", "中国石化取到自己的分 42（未抓到现价值）",
              auth.get("600028", {}).get("score") == 42,
              f"got {auth.get('600028')}")
        check("CE-02", "表格内标的未误建权威分（段边界正确）",
              "600141" not in auth and "002312" not in auth, f"got {list(auth)}")
    finally:
        shutil.rmtree(tmp)


# ══════════════════════════════════════════════════════════════
# 6. extract_scores 与权威分一致性（防解析漂移）
# ══════════════════════════════════════════════════════════════
def test_parse_alignment():
    extracted = extract_scores(REVIEW_0916)
    codes = {s.get("code"): s for s in extracted}
    check("PA-01", "extract_scores 抓到工商银行 45 分",
          codes.get("601398", {}).get("score") == 45, f"got {codes}")
    check("PA-02", "兆易创新不在审查段（未被当日审查）",
          "603986" not in codes, f"got {list(codes)}")


# ══════════════════════════════════════════════════════════════
# 7. 坑7 回归：权威分必须用完整审查报告，禁用局部摘要
# ══════════════════════════════════════════════════════════════
def test_full_report_required():
    """_extract_review 只取 145 字符局部摘要，会丢失当日已审查标的的评分。

    若 SkepticAgent 把摘要而非完整报告传入 build_authoritative_scores，
    当日已审查的工商银行会从权威表里消失 → 被误判成「池内缓存(非当日审查)」，
    进而被 stale 拦截、无法进入决策。
    """
    from skeptic_agent import SkepticAgent
    sa = SkepticAgent.__new__(SkepticAgent)
    summary = sa._extract_review(REVIEW_0916)

    tmp = Path(tempfile.mkdtemp(prefix="score_src_"))
    try:
        make_pool(tmp, [{"代码": "603986", "名称": "兆易创新", "综合分": 84,
                         "纳入日期": "2026-09-15"}])
        auth_full = ss.build_authoritative_scores(REVIEW_0916, tmp / "五池管理", TODAY)
        auth_sum = ss.build_authoritative_scores(summary, tmp / "五池管理", TODAY)

        check("FR-01", "摘要丢失当日审查标的（bug 形态复现）",
              "601398" not in auth_sum, f"got {list(auth_sum)}")
        check("FR-02", "完整报告保留当日审查标的 45 分",
              auth_full.get("601398", {}).get("score") == 45,
              f"got {auth_full.get('601398')}")
        check("FR-03", "完整报告下工商银行 stale=False（不误判）",
              auth_full["601398"]["stale"] is False)
        check("FR-04", "两条路径对兆易判定一致（池内缓存 stale）",
              auth_full["603986"]["stale"] is True
              and auth_sum["603986"]["stale"] is True)

        # 锁定 SkepticAgent 实际注入路径用的是完整报告
        sa.pool_dir = tmp / "五池管理"
        sa._last_review_report = REVIEW_0916
        stocks = [{"代码": "601398", "名称": "工商银行"}]
        table = sa._format_authoritative_scores(stocks)
        check("FR-05", "注入表含当日审查的工商银行",
              "601398" in table and "review_today" in table, f"got:\n{table}")
        check("FR-06", "注入表不含「池内缓存」误标（当日已审查）",
              "池内缓存" not in table, f"got:\n{table}")
    finally:
        shutil.rmtree(tmp)


# ══════════════════════════════════════════════════════════════
# 8. 重点观察池回补 — 审查候选覆盖缺口（遗留项3）
# ══════════════════════════════════════════════════════════════
def test_key_watch_supplement():
    """兆易(603986) 升入重点观察池后不在任何审查来源内 → 永久滞留。

    审查候选原来源：快筛候选池 ∪ 边缘池回补(<3只时)。
    兆易 ∉ 快筛候选池（fs_hist≠今日被清空），兆易 ∉ 边缘池
    → 永远进不了审查候选 → 84分(9-15)永久沿用。
    """
    from review_agent import ReviewAgent
    from thresholds import KEY_POOL_EXPIRE_DAYS
    f = ReviewAgent._supplement_from_key_watch
    TODAY = "2026-09-16"

    def mk(code, name, score=84, entry="2026-09-01"):
        return {"代码": code, "名称": name, "综合分": score, "纳入日期": entry}

    kw = [
        mk("603986", "兆易创新", 84, "2026-09-01"),   # 滞留15天 → 应回补
        mk("600001", "当日升入", 78, "2026-09-16"),    # 滞留0天 → 不回补
        mk("600002", "昨日", 70, "2026-09-15"),        # 滞留1天 =1 → 应回补（边界）
        mk("600003", "7天", 66, "2026-09-09"),         # 滞留7天 → 应回补
        mk("600004", "无日期", 90, ""),                # 日期缺失 → 不回补
    ]

    out = f([], set(), TODAY, kw)
    codes = [s["代码"] for s in out]
    check("KW-01", "回补兆易（滞留15天≥1，T+1）", "603986" in codes, f"got {codes}")
    check("KW-02", "不回补当日升入（滞留0天）", "600001" not in codes, f"got {codes}")
    check("KW-03", "边界：滞留恰1天应回补（T+1）", "600002" in codes, f"got {codes}")
    check("KW-04", "滞留7天应回补", "600003" in codes, f"got {codes}")
    check("KW-05", "不回补纳入日期缺失的标的", "600004" not in codes, f"got {codes}")
    check("KW-06", "阈值 KEY_POOL_EXPIRE_DAYS=1（T+1，对齐S池纪律）",
          KEY_POOL_EXPIRE_DAYS == 1)

    # 已存在 code 不重复回补
    out2 = f([], {"603986"}, TODAY, kw)
    check("KW-07", "已在审查候选中的不重复回补",
          "603986" not in [s["代码"] for s in out2])

    # max_add 上限
    many = [mk(f"6{i:05d}", f"股{i}", 80, "2026-09-01") for i in range(8)]
    out3 = f([], set(), TODAY, many, max_add=5)
    check("KW-08", "回补总数不超过 max_add=5", len(out3) == 5, f"got {len(out3)}")

    # 独立预算（本次修复的关键决策）：回补数量只由 max_add 决定，与 raw 现有数量无关。
    # 边缘池回补（09-14 P0-2）会先把 raw 补到 5 只；若两者共享 max_total，
    # 重点观察池就永远拿不到配额 —— 而重点观察池正是分数新鲜度最关键的池。
    # raw_full 用 600000-600003（与 many 前4只重叠），应被去重跳过 → 回补剩4只
    raw_full = [mk(f"6{i:05d}", f"快筛{i}", 80, TODAY) for i in range(4)]
    out4 = f(raw_full, {s["代码"] for s in raw_full}, TODAY, many, max_add=20)
    check("KW-09", "独立预算：raw已4只仍回补未重叠的4只",
          len(out4) == 4, f"got {len(out4)}")
    # raw 用不重叠代码 → 全部8只都能回补
    raw_other = [mk(f"6{i:05d}", f"他{i}", 80, TODAY) for i in range(8, 12)]
    out4b = f(raw_other, {s["代码"] for s in raw_other}, TODAY, many, max_add=20)
    check("KW-09b", "独立预算：raw代码不重叠时8只全回补",
          len(out4b) == 8, f"got {len(out4b)}")

    # max_add=0 → 不回补
    out5 = f([mk("699999", "满", 80, TODAY)] * 5, set(), TODAY, many, max_add=0)
    check("KW-10", "max_add=0 不回补", out5 == [])

    # 已在审查候选中的标的必须跳过（去重）
    dup = [mk("600001", "已入候选", 80, "2026-09-01"),
           mk("600002", "未入候选", 80, "2026-09-01")]
    out6 = f([mk("600001", "x", 80, TODAY)], {"600001"}, TODAY, dup, max_add=5)
    check("KW-18", "max_add 去重生效：已入候选的跳过",
          [s["代码"] for s in out6] == ["600002"], f"got {[s['代码'] for s in out6]}")

    # 空重点观察池
    check("KW-11", "重点观察池为空返回空列表", f([], set(), TODAY, []) == [])

    # 日期非法字符串不崩溃
    bad = [mk("600005", "坏日期", 80, "not-a-date")]
    check("KW-12", "非法日期不崩溃且不回补", f([], set(), TODAY, bad) == [])

    # today 非法 → 不崩溃
    check("KW-13", "today非法不崩溃", isinstance(f([], set(), "bad", kw), list))

    # 代码字段兼容「股票代码」
    alt = [{"股票代码": "600006", "名称": "别名", "综合分": 80, "纳入日期": "2026-09-01"}]
    out7 = f([], set(), TODAY, alt)
    check("KW-14", "兼容「股票代码」字段",
          out7 and (out7[0].get("股票代码") == "600006"))
    check("KW-15", "无代码字段跳过", f([], set(), TODAY, [{"名称": "无代码"}]) == [])

    # 自定义 max_age_days
    out8 = f([], set(), TODAY, kw, max_age_days=1)
    check("KW-16", "max_age_days=1 时兆易(15天)回补",
          "603986" in [s["代码"] for s in out8])
    out9 = f([], set(), TODAY, kw, max_age_days=30)
    check("KW-17", "max_age_days=30 时兆易(15天)不回补",
          "603986" not in [s["代码"] for s in out9])


# ══════════════════════════════════════════════════════════════
# 9. 回补集成的变量作用域隐患（existing_codes 未定义）
# ══════════════════════════════════════════════════════════════
def test_supplement_integration_scope():
    """existing_codes 只在「候选池<3 的边缘池回补」块内赋值。

    候选池已满 3 只时该分支不执行，变量未定义 → NameError 被外层 except
    吞掉，导致重点观察池回补静默失效。此处复现真实代码路径验证不崩溃。
    """
    from review_agent import ReviewAgent
    f = ReviewAgent._supplement_from_key_watch
    TODAY = "2026-09-16"
    kw = [{"代码": "603986", "名称": "兆易创新", "综合分": 84,
           "纳入日期": "2026-09-01"}]

    # 复现 _run_impl 的两条分支
    for pool_size in (0, 2, 5, 20):   # 0/2 < 3 走边缘回补；5/20 ≥ 3 不赋值 existing_codes
        raw = [{"代码": f"6{i:05d}", "名称": f"快筛{i}"} for i in range(pool_size)]
        existing_codes = set() if pool_size < 3 else "UNDEFINED"
        try:
            _existing = {str(s.get("代码", s.get("股票代码", ""))) for s in raw}
            out = f(raw, _existing, TODAY, kw)
            ok = isinstance(out, list)
        except NameError:
            ok = False
        check(f"KP-{pool_size}", f"候选池{pool_size}只时回补不 NameError", ok,
              f"existing_codes={existing_codes!r}")

    # 独立预算：即使 raw 已有 3 只（边缘池回补留下的），兆易仍能回补
    raw5 = [{"代码": f"6{i:05d}", "名称": f"快筛{i}"} for i in range(3)]
    _existing = {str(s.get("代码")) for s in raw5}
    out = f(raw5, _existing, TODAY, kw, max_add=20)
    check("KP-full3", "候选池满3只时兆易仍可回补（独立预算）",
          any(s["代码"] == "603986" for s in out), f"got {[s['代码'] for s in out]}")

    raw5b = [{"代码": f"6{i:05d}"} for i in range(5)]
    out2 = f(raw5b, {s["代码"] for s in raw5b}, TODAY, kw, max_add=20)
    check("KP-full5", "候选池满5只时兆易仍可回补（独立预算）",
          any(s["代码"] == "603986" for s in out2), f"got {[s['代码'] for s in out2]}")


# ══════════════════════════════════════════════════════════════
def main():
    tests = [test_authoritative, test_trace, test_clean_confidence,
             test_extract_confidence, test_no_cross_entity, test_parse_alignment,
             test_full_report_required, test_key_watch_supplement,
             test_supplement_integration_scope]
    for t in tests:
        try:
            t()
        except Exception as e:
            global FAIL
            FAIL += 1
            RESULTS.append((t.__name__, "异常", "❌", f"{type(e).__name__}: {e}"))
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 72)
    for cid, name, mark, detail in RESULTS:
        line = f"{mark} {cid:<6} {name}"
        if detail:
            line += f"  [{detail}]"
        print(line)
    print("=" * 72)
    print(f"PASS: {PASS}  FAIL: {FAIL}  TOTAL: {PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
