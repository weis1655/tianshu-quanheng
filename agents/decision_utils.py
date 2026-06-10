"""
Decision Utils - 决策工具函数（纯函数，零self依赖）
从 decision_agent.py 提取的纯函数，用于评分提取、空仓报告构建和市场环境格式化。
"""

import re
from datetime import datetime


def extract_scores(review_report: str) -> list[dict]:
    """从审查报告中提取结构化评分（正则提取，无LLM）"""
    stocks = []
    # 按 ## 标题分割，每个section是一个股票
    sections = re.split(r"(?<=\n)(?=## )", review_report)
    for section in sections:
        if not section.strip():
            continue
        # 提取标题行中的代码和名称（标题格式无括号：`## 600118 中国卫星`）
        first = section.strip().split("\n")[0]
        m = re.match(r"##\s*\[?(\d{6})\]?\s*[（(]?\s*([\u4e00-\u9fa5]{2,10})", first)
        if not m:
            continue
        code, name = m.group(1), m.group(2)
        if code == "000000":
            continue
        # 提取评分：先找含"综合评分"的行，再提取数字（兼容加粗/非加粗格式）
        score = None
        for line in section.split("\n"):
            if "综合评分" in line:
                # 格式1：`**XX**`（加粗）
                sm = re.search(r"\*\*(\d{2,3})\*\*", line)
                if sm:
                    score = int(sm.group(1))
                    break
                # 格式2：`综合评分：75` 或 `综合评分：75分`（非加粗，P0-2修复）
                nm = re.search(r"综合评分[：:\s*](\d{2,3})", line)
                if nm:
                    score = int(nm.group(1))
                    break
                # 格式3：行内任意两位/三位数（兜底）
                fallback = re.search(r"\b(\d{2,3})\b", line)
                if fallback:
                    s = int(fallback.group(1))
                    if 40 <= s <= 100:  # 合理评分范围过滤
                        score = s
                        break
                    break
        if score is None or score > 100:
            continue
        # 流转方向
        flow = re.search(r"→\s*(升级|通过|关注)", section)
        # 信心度
        conf = re.search(r"信心[度理].*?[:：]\s*([^\n]{2,20})", section)
        # P1-修复: 过滤无效前缀（"关于该股票""包含***""无推荐"等伪名称）
        invalid_prefixes = ("关于", "包含", "未包含", "无")
        if name and any(name.startswith(p) for p in invalid_prefixes):
            continue
        stocks.append({
            "code": code, "name": name, "score": score,
            "passed": flow is not None,
            "confidence": conf.group(1).strip() if conf else ""
        })
    stocks.sort(key=lambda x: x["score"], reverse=True)
    # ── v5.91: 补充解析重点观察池评估表格（表中无 ## 标题的股票章节）────
    # 兼容审查报告.md末尾追加的重点观察池评估表格
    table_section = re.search(r"## 📋 重点观察池最新评估\n.*?(?=\n## |\Z)", review_report, re.DOTALL)
    if table_section:
        existing_codes = {s["code"] for s in stocks}
        for line in table_section.group(0).split("\n"):
            # 匹配表格行：| 股票名(CODE) | 综合分 | 信心度 | ...
            tm = re.match(r"\|\s*([\u4e00-\u9fa5a-zA-Z]+)\s*[（(]?(\d{6})[）)]?\s*\|", line)
            if tm:
                name, code = tm.group(1), tm.group(2)
                if code in existing_codes:
                    continue  # 主审查已提取，跳过
                # 从表格列提取综合分
                cols = [c.strip() for c in line.split("|") if c.strip()]
                score = None
                for i, col in enumerate(cols):
                    if col == code:
                        # 综合分通常在第2列（code之后）
                        if i + 1 < len(cols) and re.match(r"^\d{2,3}$", cols[i+1]):
                            s = int(cols[i+1])
                            if 40 <= s <= 100:
                                score = s
                        break
                if score is not None:
                    stocks.append({
                        "code": code, "name": name, "score": score,
                        "passed": True,
                        "confidence": cols[3] if len(cols) > 3 else ""
                    })
    stocks.sort(key=lambda x: x["score"], reverse=True)
    return stocks


def build_empty_decision(today: str, pools: dict, market_env: str, reason: str,
                         yellow_alerts: list | None = None) -> str:
    """二审制Gate：所有标的被拦截时生成空仓决策报告（返回报告文本）"""
    # 构建池状态文本（在f-string前完成）
    pool_text_list = []
    for name, data in pools.items():
        stocks = data.get("stocks", []) if isinstance(data, dict) else []
        pool_text_list.append(f"{name}({len(stocks)}只)")
    pool_text = " | ".join(pool_text_list) if pool_text_list else "（无数据）"

    # 构建备选观察文本
    alert_text = ""
    if yellow_alerts:
        alert_lines = ["\n\n## 🟡 备选观察标的（60-74分黄色预警）\n"]
        for s in yellow_alerts[:5]:  # 最多5只
            alert_lines.append(f"- {s['name']}({s['code']}) {s['score']}分")
        if len(yellow_alerts) > 5:
            alert_lines.append(f"- ...另有{len(yellow_alerts)-5}只")
        alert_text = "\n".join(alert_lines)

    report = f"""# 【决策报告】{today}

━━━━━━━━━━━━━━━━

## 🔴 空仓 — 二审制Gate拦截

**原因**：{reason}

---

### 大盘环境
{market_env}

---

### 当前池状态
{pool_text}{alert_text}
---

决策执行时间：{datetime.now().strftime('%H:%M')}
"""
    return report


def format_market_env() -> str:
    """大盘环境兜底模板"""
    return """- **上证指数**：震荡整理，4000-4100区间波动
- **创业板指**：创新高后回调，短期偏谨慎
- **市场状态**：分化格局，强者恒强
- **环境评级**：震荡偏强，仓位建议单票10-20%，总仓位30%"""