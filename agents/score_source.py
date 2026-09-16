"""
Score Source - 评分溯源与字段校验（纯函数，零 self 依赖）

背景（2026-09-16 兆易创新 84分/45分矛盾）：
审查层当日只审了工商银行(601398)=45分，兆易创新(603986) 未被审查。
兆易的 84 分来自重点观察池缓存（9-15 旧分）。
质疑层与决策层从 LLM 自由文本里读到"前次45分"，并把工商的 45 分套用给兆易，
最终决策报告输出「兆易创新(45分)、工商银行(45分)」——两个分数并非同一标的。

本模块的四条防线：
1. build_authoritative_scores()  —— 权威评分从结构化来源（审查报告段落 + 池 JSON）取，
                                      LLM 无权自述历史评分
2. trace_decision_scores()        —— 决策报告里写出的每个分数必须能在权威来源找到
                                      对应 code 的行，找不到即标记可疑
3. clean_confidence()             —— 信心度字段对齐校验，防解析截断（"高"字丢失）
4. build_authoritative_scores()   —— 池内缓存分标记 stale，不参与「评分≥75 可执行」
                                     （stale 判定在 decision_agent 以权威来源优先落地）

设计原则：宁可 None / 保守值，不可跨实体取分（见 tianshu-subsystem-development 陷阱 7.6/7.7）。
"""

import re
from pathlib import Path
from datetime import datetime

# 分数有效区间
SCORE_MIN, SCORE_MAX = 0, 100
# 段内锚定：只在本标段落内取分，禁止全局回退（避免抓到相邻行的数值）
_SECTION_SPLIT = re.compile(r'(?m)^##\s*\[?(\d{6})\]?')


def _load_json(path: Path, default: dict):
    """安全读 JSON，任何异常都返回 default（不抛）。"""
    try:
        import json
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _score_from_block(block: str):
    """在单个 `## code name` 段内提取综合分。取不到返回 None。"""
    if not block:
        return None
    for line in block.split("\n"):
        if "综合评分" not in line:
            continue
        # 格式1：`**45**`（加粗）
        m = re.search(r"\*\*(\d{2,3})\*\*", line)
        if m:
            return _clip(int(m.group(1)))
        # 格式2：`综合评分：45` 或 `综合评分：45分`
        m = re.search(r"综合评分[：:\s*](\d{2,3})", line)
        if m:
            return _clip(int(m.group(1)))
        # 格式3：行内兜底（仅在 40-100 合理区间接受，防止抓到 PE/日期）
        m = re.search(r"\b(\d{2,3})\b", line)
        if m and 40 <= int(m.group(1)) <= 100:
            return int(m.group(1))
    return None


def _clip(v, default=None):
    if isinstance(v, bool) or v is None:
        return default
    try:
        return max(SCORE_MIN, min(SCORE_MAX, int(v)))
    except (TypeError, ValueError):
        return default


def _split_sections(report: str) -> dict:
    """把审查报告切成 {code: block}。段边界 = 下一个 `## 6位代码` 或文末。"""
    if not report:
        return {}
    out = {}
    marks = list(_SECTION_SPLIT.finditer(report))
    for i, m in enumerate(marks):
        code = m.group(1)
        if code == "000000":
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(report)
        out.setdefault(code, report[m.start():end])
    return out


def build_authoritative_scores(review_report: str, pool_dir, today: str = None) -> dict:
    """构建权威评分快照（结构化来源，LLM 无权自述）。

    Args:
        review_report: 当日审查报告全文（str）
        pool_dir:      五池目录（Path 或 str），用于读取池内缓存分
        today:         当日日期 "YYYY-MM-DD"，默认取系统当天

    Returns:
        {code: {
            "code": str, "name": str,
            "score": int|None,        # 权威分（当日审查分优先）
            "source": "review_today" | "pool_cache" | "none",
            "review_date": str,       # 分数产生日期（stale 判定依据）
            "stale": bool,            # True = 池内旧分，非当日审查
        }}
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    if isinstance(pool_dir, str):
        pool_dir = Path(pool_dir)

    scores = {}
    # ── 源1：当日审查报告 `## 601398 工商银行` 段（权威）──
    for code, block in _split_sections(review_report).items():
        head = block.strip().split("\n")[0]
        nm = re.search(r"\d{6}\s*[（(]?([\u4e00-\u9fa5]{2,10})", head)
        scores[code] = {
            "code": code,
            "name": nm.group(1) if nm else "?",
            "score": _score_from_block(block),
            "source": "review_today",
            "review_date": today,
            "stale": False,
        }

    # ── 源2：重点观察池缓存分（池内但当日未审查 → stale）──
    pool_file = pool_dir / "重点观察池.json"
    pool_data = _load_json(pool_file, {})
    for s in (pool_data.get("stocks", []) if isinstance(pool_data, dict) else []):
        code = str(s.get("代码", s.get("股票代码", ""))).strip()
        if not re.fullmatch(r"\d{6}", code):
            continue
        if code in scores:
            # 当日已审查：以审查分为准，补充入池日期
            scores[code]["review_date"] = scores[code].get("review_date") or s.get("纳入日期", today)
            continue
        entry = s.get("纳入日期", "")
        scores[code] = {
            "code": code,
            "name": s.get("名称", "?"),
            "score": _clip(s.get("综合分", s.get("综合评分", s.get("score", None)))),
            "source": "pool_cache",
            "review_date": entry or "unknown",
            "stale": bool(entry) and entry != today,
        }
    return scores


def clean_confidence(confidence, score=None):
    """信心度字段对齐校验（修复 2026-09-16「高」字丢失）。

    异常形态（解析错位/截断）→ 用分数推导默认值，宁可保守不可脏数据入池：
    - None / 空串
    - 以标点或空白开头（"，逻辑通顺" ← "高" 被截断）
    - 长度 < 2

    Returns: (cleaned_value, was_dirty)
    """
    def _default():
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            return "高" if score >= 75 else ("中" if score >= 60 else "低")
        return "中"

    LEVELS = ("中高", "中低", "高", "中", "低")
    if confidence is None:
        return _default(), True
    v = str(confidence).strip()
    # 单字级别词是合法值（LLM 常只输出「高」/「中」），不能被 len<2 误杀
    if v in LEVELS:
        return v, False
    if len(v) < 2 or v[0] in "，。；、,.:;:!?？！~～\t\r\n":
        return _default(), True
    return v, False


def trace_decision_scores(decision_text: str, authoritative: dict) -> list:
    """溯源决策报告里的分数声明。

    规则：报告里 `名称(代码) ... N分` 的声明，N 必须等于该 code 的权威分；
    权威分缺失（当日未审查）时，声明本身即不可信。

    Returns: [
        {"code": str, "name": str, "declared": int,
         "authoritative": int|None, "source": str,
         "valid": bool, "issue": str}
    ]
    """
    if not decision_text:
        return []
    issues = []
    seen = set()

    # Step 1: 先建 name→code 映射（报告里 `兆易创新（603986）` 形式）
    name2code = {}
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,10})\s*[（(]\s*(\d{6})\s*[）)]", decision_text):
        name2code.setdefault(m.group(1), m.group(2))
    for c, v in authoritative.items():
        name2code.setdefault(v.get("name", "?"), c)

    # Step 2: 两种声明形式
    #   形式A: `兆易创新（603986）... 45分` → code 紧邻名称，分数在 0-60 字内
    #   形式B: `兆易创新(45分)` / `兆易创新（45分）` → 括号内直接是分数，code 靠映射反查
    #           （2026-09-16 真实漏报形态：括号里是分数而非6位代码）
    pat_a = re.compile(r"([\u4e00-\u9fa5]{2,10})\s*[（(]\s*(\d{6})\s*[）)]\s*[^\n]{0,60}?(\d{2,3})\s*分")
    pat_b = re.compile(r"([\u4e00-\u9fa5]{2,10})\s*[（(]\s*(\d{2,3})\s*分\s*[）)]")

    for pat, is_b in ((pat_a, False), (pat_b, True)):
        for m in re.finditer(pat, decision_text):
            name = m.group(1)
            code = m.group(2) if not is_b else name2code.get(m.group(1))
            if code is None or code in seen:
                continue
            seen.add(code)
            declared = _clip(m.group(3) if not is_b else m.group(2))
            if declared is None:
                continue
            auth = authoritative.get(code)
            auth_score = auth.get("score") if auth else None
            src = auth.get("source", "none") if auth else "none"
            if auth is None:
                valid, issue = False, "当日审查报告无此标的，分数来源不明（疑似跨标的套用）"
            elif auth_score is None:
                valid, issue = False, "权威分缺失（解析失败），无法核对"
            else:
                valid = declared == auth_score
                tail = "（池内缓存分，非当日审查）" if auth.get("stale") else ""
                issue = "" if valid else f"声明{declared}分≠权威{auth_score}分{tail}"
            issues.append({
                "code": code, "name": name or (auth or {}).get("name", "?"),
                "declared": declared, "authoritative": auth_score,
                "source": src, "valid": valid, "issue": issue,
            })
    return issues
