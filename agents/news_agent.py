#!/usr/bin/env python3
"""
News Agent - 新闻分析 Agent（重构版 v3.1）
核心原则：新闻是天枢系统的前提。无有效新闻则终止全流程。

数据源策略（按时段）：
- 白天(8:00-19:00): 新浪快讯 + 同花顺快讯（实时财经）
- 晚间(19:00+): DuckBurn 联播完整版 + 快讯补充
- 全天兜底: 东方财富快讯

质量门控：必须同时满足
  1. 内容字数 >= 500
  2. 有效新闻条数 >= 5（快讯格式）
  3. 无 "404" / "无新闻数据" / "Not Found" 等失败标记
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import requests
from logger import plog

from base_agent import BaseAgent, build_agent_system_prompt
from logger import StructuredLogger

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))
from path_config import ensure_agent_paths; ensure_agent_paths()


# ─── 质量检测 ─────────────────────────────────────────────

def is_valid_news(content: str, min_len: int = 500, min_items: int = 5) -> tuple[bool, str]:
    """
    严格质量门控，同时检查：
    1. 字数下限（联播500字，快讯300字）
    2. 新闻条数或联播特征（联播格式: 关键词，快讯格式: 【或[时间]）
    3. 无失败标记
    返回 (是否有效, 诊断信息)
    """
    if not content:
        return False, "空内容"

    # 3. 失败标记检测
    fail_markers = ["404", "Not Found", "无新闻数据", "请求失败",
                    "DOMContentLoaded", "querySelector", "<html>", "network error"]
    for marker in fail_markers:
        if marker.lower() in content.lower()[:500]:
            return False, f"含失败标记: {marker}"

    # 判断内容格式：联播 vs 快讯
    is_broadcast = bool(re.search(r"新闻联播|联播|主要内容", content[:300]))
    is_fast = content.count("【") > 0 or re.search(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}", content) is not None

    # 1. 字数下限（快讯300，联播500）
    min_len_effective = 300 if is_fast else 500
    if len(content) < min_len_effective:
        return False, f"字数不足: {len(content)} < {min_len_effective}"

    # 2. 快讯条数检测（快讯格式）| 联播内容检测（联播格式）
    fast_count = content.count("【") + content.count("[20")
    broadcast_count = len(re.findall(r"(?:^|\n)(?!　*)[^\n]{10,50}(?:\n|$)", content))  # 估计段落数

    if is_fast:
        # 快讯：先看条数，够了就算（快讯每条短，条数是主指标）
        if fast_count >= min_items:
            return True, f"OK (快讯 {len(content)}字, {fast_count}条)"
        if len(content) < 300:
            return False, f"字数不足: {len(content)}字且快讯<{min_items}条"
    elif not is_broadcast:
        # 非联播非快讯，内容太短
        if len(content) < 800:
            return False, f"字数不足: {len(content)} < 800"

    # 至少要有联播特征或快讯特征之一
    if not is_broadcast and not is_fast:
        return False, f"无新闻特征（联播/快讯格式）: 仅{len(content)}字纯文本"

    return True, f"OK ({'联播' if is_broadcast else '快讯'} {len(content)}字)"


# ─── 数据源 ───────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def fetch_from_duckburn() -> tuple[str, str]:
    """DuckBurn GitHub 新闻联播（Markdown）— 联播完整版，质量最高"""
    today = datetime.now()
    for offset in [0, 1, 2]:  # 今天/昨天/前天
        check_date = today - timedelta(days=offset)
        date_str = check_date.strftime("%Y%m%d")
        url = f"https://raw.githubusercontent.com/DuckBurnIncense/xin-wen-lian-bo/master/news/{date_str}.md"
        try:
            r = requests.get(url, timeout=15, headers=HEADERS)
            if r.status_code == 200 and len(r.text) > 500:
                # 质量检查：必须有新闻联播关键字
                if "新闻联播" in r.text or "联播" in r.text or "主要内容" in r.text:
                    return r.text, f"DuckBurn({date_str})"
        except Exception:  # 安全降级: DuckBurn API 失败→降级到下一个数据源
            pass
    return "", "DuckBurn"


def fetch_from_govopendata() -> tuple[str, str]:
    """govopendata.cn 新闻联播（HTML）— 备用，质量差"""
    today = datetime.now()
    for offset in [0, 1]:
        check_date = today - timedelta(days=offset)
        date_str = check_date.strftime("%Y%m%d")
        url = f"https://cn.govopendata.com/xinwenlianbo/{date_str}/"
        try:
            r = requests.get(url, timeout=15, headers=HEADERS)
            if r.status_code != 200:
                continue
            raw = r.text
            # 提取正文
            text = re.sub(r'<[^>]+>', '', raw)
            text = re.sub(r'\s+', '\n', text).strip()
            # 过滤JS/CSS噪音
            noise = ['DOMContentLoaded', 'querySelector', 'function ', 'const ', 'let ', 'var ']
            if sum(1 for n in noise if n in text) >= 3:
                continue
            if len(text) > 500 and ("联播" in text or "新闻" in text[:500]):
                return text[:5000], f"govopendata({date_str})"
        except Exception:  # 安全降级: govopendata API 失败→降级到下一个数据源
            pass
    return "", "govopendata"


def fetch_from_sina_news() -> tuple[str, str]:
    """新浪财经快讯 — 实时财经新闻（白天首选）"""
    url = ("https://zhibo.sina.com.cn/api/zhibo/feed"
           "?column=finance&zhibo_id=152&page=1&page_size=50&tag_id=0&dire=f&dpc=1&pagesize=50")
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code != 200:
            return "", "sina_news"
        data = r.json()
        items = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
        # 筛选今日新闻
        today_items = [
            f"[{item.get('create_time', '')}] {item.get('rich_text', '')}"
            for item in items
            if item.get("create_time", "").startswith(today_str)
        ]
        if today_items:
            return "\n".join(today_items), "sina_news"
    except Exception:  # 安全降级: 新浪新闻 API 失败→降级到下一个数据源
        pass
    return "", "sina_news"


def fetch_from_tonghuashun() -> tuple[str, str]:
    """同花顺财经快讯 — 实时财经新闻（白天备用）"""
    url = "https://news.10jqka.com.cn/tapp/news/push/stock/?page=1&tag=&track=website&pagesize=30"
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code != 200:
            return "", "tonghuashun"
        data = r.json()
        items = data.get("data", {}).get("list", []) if isinstance(data.get("data"), dict) else []
        # 优先选今日且有关键字的
        news_items = []
        for item in items:
            title = str(item.get("title", item) if isinstance(item, dict) else item)
            ct = str(item.get("time", item.get("ctime", "")) if isinstance(item, dict) else "")
            if today_str[:7] in ct or not ct:  # 今日或无时间（默认最新）
                news_items.append(title)
        if not news_items:
            # fallback: 取全部
            news_items = [
                str(item.get("title", item) if isinstance(item, dict) else item)
                for item in items[:20]
            ]
        if news_items:
            content = f"=== 同花顺财经快讯 {today_str} ===\n" + "\n".join(f"- {t}" for t in news_items[:25])
            return content, "tonghuashun"
    except Exception:  # 安全降级: 同花顺 API 失败→降级到下一个数据源
        pass
    return "", "tonghuashun"


def fetch_from_eastmoney() -> tuple[str, str]:
    """东方财富快讯 — 全天候实时（最稳定的快讯源）"""
    # 尝试多个东方财富接口
    apis = [
            "https://np-listapi.eastmoney.com/comm/web/getFastNewsList?client=web&biz=web_home&page=1&pageSize=30&sortEnd=&fastColumn=&startTime=&endTime=",
        ]
    today_str = datetime.now().strftime("%Y-%m-%d")
    for url in apis:
        try:
            r = requests.get(url, timeout=10, headers=HEADERS)
            if r.status_code != 200:
                continue
            text = r.text
            if "404" in text[:200]:
                continue
            # 尝试JSON解析
            try:
                data = r.json()
                # 通用JSON字段提取
                items = []
                for val in data.values():
                    if isinstance(val, list):
                        items.extend(val)
                    elif isinstance(val, dict):
                        for v in val.values():
                            if isinstance(v, list):
                                items.extend(v)
                if len(items) >= 5:
                    content_items = []
                    for item in items[:20]:
                        if isinstance(item, dict):
                            title = item.get("title", item.get("content", item.get("text", "")))
                            if title:
                                content_items.append(str(title))
                    if content_items:
                        return "\n".join(f"- {t}" for t in content_items), "eastmoney"
            except (ValueError, TypeError):
                # 非JSON，尝试提取文本
                text_clean = re.sub(r'<[^>]+>', '', text)
                text_clean = re.sub(r'\s+', '\n', text_clean).strip()
                if len(text_clean) > 200:
                    return text_clean[:5000], "eastmoney"
        except Exception:  # 安全降级: 东方财富 API 失败→降级到下一个数据源
            pass
    return "", "eastmoney"


def fetch_news_broadcast() -> tuple[str, str]:
    """
    多源智能合并策略（按时段 + 严格质量门控）
    返回: (内容, 来源描述)
    关键：必须通过 is_valid_news 质量门控，否则返回空
    """
    now = datetime.now()
    hour = now.hour

    # 时段1: 白天(5:00-19:00) → 快讯优先
    if 5 <= hour < 19:
        fast_sources = [
            ("sina_news", fetch_from_sina_news),
            ("tonghuashun", fetch_from_tonghuashun),
            ("eastmoney", fetch_from_eastmoney),
        ]
        fast_results = []
        for name, fetcher in fast_sources:
            content, detail = fetcher()
            valid, reason = is_valid_news(content, min_len=300, min_items=3)
            plog("INFO", f"  [News] {name}: {reason}")
            if valid:
                fast_results.append((name, detail, content))

        # 多源合并（节省token，只合并两个最好的）
        if fast_results:
            # 优先选sina_news（最大），最多合并2个
            fast_results.sort(key=lambda x: len(x[2]), reverse=True)
            top = fast_results[:2]
            if len(top) >= 2:
                merged = top[0][2] + "\n\n=== 补充来源 ===\n" + top[1][2]
                return merged, f"{top[0][1]}+{top[1][1]}"
            else:
                return top[0][2], top[0][1]

    # 时段2: 晚间(19:00-05:00) → 联播优先
    broadcast_sources = [
        ("DuckBurn", fetch_from_duckburn),
        ("govopendata", fetch_from_govopendata),
    ]
    for name, fetcher in broadcast_sources:
        content, detail = fetcher()
        valid, reason = is_valid_news(content, min_len=500, min_items=3)
        plog("INFO", f"  [News] {name}: {reason}")
        if valid:
            # 联播太短时，用快讯补充
            if len(content) < 1000:
                sina_content, _ = fetch_from_sina_news()
                if sina_content and is_valid_news(sina_content)[0]:
                    content = content + "\n\n=== 补充快讯 ===\n" + sina_content
                    detail = detail + "+sina"
            return content, detail

    # 兜底：遍历所有源，不限时段
    all_sources = [
        ("DuckBurn", fetch_from_duckburn),
        ("sina_news", fetch_from_sina_news),
        ("tonghuashun", fetch_from_tonghuashun),
        ("eastmoney", fetch_from_eastmoney),
        ("govopendata", fetch_from_govopendata),
    ]
    for name, fetcher in all_sources:
        content, detail = fetcher()
        valid, reason = is_valid_news(content, min_len=300, min_items=3)
        plog("INFO", f"  [News] (兜底) {name}: {reason}")
        if valid:
            return content, detail

    return "", "无有效数据"


# ─── 分析逻辑 ─────────────────────────────────────────────

ROLE_PROMPT = """你是一个专业的金融新闻分析师，专门从新闻中挖掘投资驱动。

你的任务：
1. 从新闻中提取所有可能影响A股市场的信息
2. 按驱动级别汇总输出（S/A/B/C四级，每个级别可以有多条）
3. 对每条驱动必须画出传导链：政策/数据 → 行业 → 个股

## 🚨 强制规则：传导链不可省略

**传导链必须包含关键词"传导链"和箭头"→"。**
**严禁跳过传导链。** 即使只有一级驱动，也要写出对应的传导链。

如果输出中没有"传导链"关键词和"→"，本回答将被判为无效。
每条的传导链必须写出完整推导：原因 → 路径 → 影响

输出格式（严格按此格式，不要给每条新闻单独打标签）：

## S级驱动
- **标题摘要**：内容说明
  - 传导链：原因 → 路径 → 影响

## A级驱动
- **标题摘要**：内容说明
  - 传导链：原因 → 路径 → 影响

## B级驱动
（如果无内容，省略该级别）

## C级驱动（风险提示）
（如果无内容，省略该级别）

示例（完整）：

## S级驱动
- **公募基金规模首破39万亿元**：权益市场资金面改善，股市流动性支撑
  - 传导链：公募规模扩张 → 权益资金增加 → 券商/基金重仓受益
- **MiniMax ARR 60天翻番**：AI赛道景气度持续验证
  - 传导链：AI收入爆发 → 算力需求增长 → 光模块/算力基建受益

## A级驱动
- **存储芯片涨价预期**：三星罢工致内存供应收缩
  - 传导链：三星罢工 → 内存供应收缩 → 存储芯片/模组涨价

如果你认为没有足够新闻支撑任何驱动，请输出："""

USER_PROMPT_TEMPLATE = """请分析以下新闻内容，输出驱动标签和传导链：

{news_content}

如果新闻内容不足，请输出：[无新闻数据]"""


class NewsAgent(BaseAgent):
    """新闻分析 Agent（继承BaseAgent）"""

    def __init__(self, agent_name: str = "NewsAgent"):
        super().__init__(agent_name)
        self.history_dir = self.root / "data" / "历史记录"
        self.logger = StructuredLogger("NewsAgent")

    def run(self, source: str = "news_broadcast", news_content: Optional[str] = None, wake_ctx: str = "") -> dict:
        """
        执行新闻分析。
        核心原则：无有效新闻直接返回失败，不往后走。
        """
        with self.logger.agent_action("run", source=source):
            return self._run_impl(source, news_content, wake_ctx)

    def _run_impl(self, source: str, news_content: Optional[str], wake_ctx: str = "") -> dict:
        # 1. 获取新闻（若无外部传入）
        with self.logger.agent_action("fetch_news"):
            if news_content is None:
                plog("INFO", "[News] 开始获取新闻（多源探测）...")
                news_content, source_desc = fetch_news_broadcast()
            else:
                source_desc = "manual"

            # ── 严格质量门控 ──────────────────────────────
            valid, reason = is_valid_news(news_content, min_len=300, min_items=3)
            if not valid:
                msg = f"新闻质量不合格: {reason}，终止后续流程"
                plog("INFO", f"[News] ❌ {msg}")
                self.logger.warning("news_quality_failed", reason=reason, length=len(news_content) if news_content else 0)
                return {
                    "success": False,
                    "error": msg,
                    "source": source_desc,
                    "quality_check": reason,
                }
            plog("INFO", f"[News] ✅ 质量通过: {reason}")
            # ── 门控结束 ─────────────────────────────────

        # 2. LLM 分析
        self.logger.llm_call("analyze_news", tokens=len(news_content))
        # P0-3: 用智能截断，保护传导链分析不被切断
        display_content = self._smart_truncate(news_content)
        user_prompt = USER_PROMPT_TEMPLATE.format(news_content=display_content)
        result = self.call_llm(user_prompt, system=build_agent_system_prompt(ROLE_PROMPT, "NewsAgent", extra_context=wake_ctx), max_tokens=1800)

        # 3. 格式化报告（P0-1: 去冗余元信息，P0-2: 去HTML标签）
        today = datetime.now().strftime("%Y-%m-%d")
        # P0-2: 去掉HTML标签，输出纯文本
        clean_preview = self._strip_html_tags(news_content[:5000]) if news_content else ""
        report = f"""# 【宏观前置分析】{today}

## 原始新闻摘要
（供下游Agent阅读，非LLM总结）
{clean_preview}

## 新闻驱动分级

{self._parse_drivers(result)}

## 传导链分析

{self._parse_chains(result)}

---
分析时间：{datetime.now().strftime('%H:%M')}
"""

        # 4. 保存
        self.history_dir.mkdir(parents=True, exist_ok=True)
        out_file = self.history_dir / f"{today}_宏观前置分析.md"
        self.safe_write_text(out_file, report)

        self.logger.info("analysis_complete",
                         source=source_desc,
                         saved_to=str(out_file),
                         stats=self.get_stats())

        return {
            "success": True,
            "report": report,
            "raw_analysis": result,
            "saved_to": str(out_file),
            "source": source_desc,
            "news_length": len(news_content),
        }

    def _parse_drivers(self, text: str) -> str:
        """
        解析 LLM 输出的驱动分级。
        兼容三种格式：
        1. 旧格式: - **S级**：标题摘要（同行的冒号后内容）
        2. 新格式: ## S级驱动 换行后 - **标题**：内容
        3. 混合格式: 新旧混合
        """
        if "[LLM调用失败]" in text or "[无新闻数据]" in text or "[模型返回空内容]" in text:
            return text

        # 检查是否有 S/A/B/C 级标记
        if not any(f"{l}级" in text for l in ["S", "A", "B", "C"]):
            return f"- ⚠️ LLM未输出S/A/B/C分级标记，内容可能为空。"

        lines_out = []
        for level_mark in ["S级", "A级", "B级", "C级"]:
            if level_mark not in text:
                continue

            # 找到该级别下有效的列表项（- ** 或 - 开头的内容）
            idx = text.index(level_mark)
            # 从该级别位置往后找
            rest = text[idx + len(level_mark):]
            # 截取到下一个级别或末尾
            for next_level in ["## S级", "## A级", "## B级", "## C级"]:
                if next_level in rest:
                    rest = rest[:rest.index(next_level)]
                    break

            # 在 rest 中找所有 - ** 开头的行（标准列表项）
            items = re.findall(r'-\s+\*\*([^*]+)\*\*\s*[：:]\s*(.+?)(?=\n-\s+\*\*|\n##|\n$)', rest, re.DOTALL)
            if not items:
                # 尝试旧格式: 紧跟在 S级：后面的单行内容
                old_match = re.search(r'[：:]\s*([^\n]+)', rest.split('\n')[0])
                if old_match:
                    content = old_match.group(1).strip()
                    if len(content) >= 5:
                        items = [(content[:30], content)]

            # 再尝试更宽松的匹配: - 开头的行（可能没有加粗）
            if not items:
                bare_items = re.findall(r'-\s+([^\n]+)', rest)
                for bi in bare_items:
                    bi = bi.strip()
                    if len(bi) < 5:
                        continue
                    if any(p in bi for p in ["（国家级", "（暂", "（传导", "暂无数据"]):
                        continue
                    title = bi[:40]
                    items.append((title, bi))

            for title, content in items:
                title = title.strip().lstrip("：: ")
                content = content.strip()
                lines_out.append(f"- **{level_mark}**：{title} - {content[:60]}")

        if not lines_out:
            fallback = re.findall(r'-\s+\*\*([^*]+?)\*\*', text)
            if fallback:
                for f_item in fallback[:5]:
                    lines_out.append(f"- **驱动**：{f_item.strip()}")
            else:
                return f"- ⚠️ LLM解析异常，未提取到有效驱动标签。"

        return "\n".join(lines_out[:10])

    def _strip_html_tags(self, text: str) -> str:
        """P0-2: 去掉HTML标签，保留纯文本（P0-2）"""
        import re
        # 去掉常见HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        # 清理多余的空白行
        text = re.sub(r'\n{3,}', '\n\n', text)
        # 清理行首行尾空白
        lines = [l.strip() for l in text.split('\n')]
        return '\n'.join(l for l in lines if l)

    def _smart_truncate(self, text: str, max_chars: int = 6000) -> str:
        """
        P0-3: 智能截断——优先保留传导链分析。
        如果文章太长，优先截断"详细新闻"区，保护"传导链分析"和"新闻摘要"不被切断。
        """
        # 传导链分析是LLM输出的核心，必须完整保留
        # P0-3 修复：chain_marker使用数据源中实际存在的分隔标记
        # 优先使用数据源自然分割点
        for marker in ["## 原始新闻摘要", "=== 补充来源 ===", "=== 补充快讯 ===", "---"]:
            if marker in text and len(text) > max_chars:
                cutoff = text.index(marker)
                # 从marker之前截断，保留传导链分析区
                prefix = text[:cutoff]
                if len(prefix) > max_chars // 2:
                    return prefix[:max_chars]
                return prefix
        # 无marker时的纯截断
        return text[:max_chars] if len(text) > max_chars else text

    def _parse_chains(self, text: str) -> str:
        # 统一箭头格式（→ -> => --> ⇒ 等）
        normalized = text.replace("->", "→").replace("=>", "→")
        normalized = normalized.replace("-->", "→").replace("⇒", "→")

        # ── 主线：从 LLM 输出中提取传导链 ────────────────────
        # 优先：传导链关键词 + 箭头或中文因果动词
        CHAIN_VERBS = ["导致", "引发", "传导至", "传到", "带动", "推动", "促使"]
        if "传导链" in normalized:
            parts = normalized.split("传导链")
            for p in parts[1:]:
                chains = []
                for l in p.split("\n"):
                    if "→" in l or any(v in l for v in CHAIN_VERBS):
                        cleaned = l.strip().lstrip("：:，, \t")
                        if cleaned:
                            chains.append(cleaned)
                if chains:
                    return "```\n" + "\n".join(chains[:8]) + "\n```"

        # 次优：仅有箭头（无传导链关键词，可能是简化格式）
        if "→" in normalized or any(v in normalized for v in CHAIN_VERBS):
            chains = []
            for l in normalized.split("\n"):
                if "→" in l or any(v in l for v in ["导致", "引发", "传导至", "传到", "带动", "推动", "促使"]):
                    cleaned = l.strip().lstrip("：:，, \t")
                    if cleaned:
                        chains.append(cleaned)
            if chains:
                return "```\n" + "\n".join(chains[:8]) + "\n```"

        # ── 降级：LLM 未输出传导链，但驱动分级可用 ───────────
        # 检查是否有 S/A/B/C 级驱动分级内容（兼容有无粗体格式）
        has_drivers, bold_titles, _ = self._detect_driver_levels(normalized)
        has_items = bool(bold_titles)
        has_simple_items = bool(re.findall(r'[SABC]级\**\s*[：:]\s*\S{5,}', normalized))

        if has_drivers and (has_items or has_simple_items):
            # 从驱动分级中提取标题
            titles = re.findall(r'\*\*([^*]+)\*\*', normalized)
            # 过滤掉纯"S级"/"A级"等分级标记，只保留实际内容
            titles = [t for t in titles if not re.match(r'^[SABC]级$', t.strip())]
            if not titles:
                titles = re.findall(r'[SABC]级\**\s*[：:]\s*([^\n]{5,40})', normalized)
            summary = "；".join(t[:80] for t in titles[:5]) if titles else "有驱动分级输出"
            return (
                "（LLM未输出传导链分析，以下为按驱动级别分类的内容摘要）\n"
                "```\n" + summary + "\n```\n"
                "> 注：LLM本次未生成'原因 → 路径 → 影响'的传导链推导，\n"
                "> 请基于驱动分级自行推导传导关系。\n"
            )

        # ── 兜底：无传导链也无驱动分级 ────────────────────────
        # 检查原始文本中是否至少含有 S/A/B/C 级别标记（宽松版）
        if re.search(r'[SABC]\s*级', normalized):
            # 有级别标记但格式没被前面正则抓到 → 摘取级别标签附近的文本
            snippets = re.findall(r'.{0,30}[SABC]\s*级.{0,60}', normalized)[:3]
            summary = "\n".join(s.strip() for s in snippets)
            return (
                "（LLM未输出标准传导链格式，以下为检测到的驱动级别片段）\n"
                "```\n" + summary + "\n```\n"
                "> 注：LLM本次未生成'原因 → 路径 → 影响'的传导链推导，\n"
                "> 请基于驱动分级自行推导传导关系。\n"
            )
        return "（传导链待补充）"

    @staticmethod
    def _detect_driver_levels(text: str) -> tuple[bool, list[str], list[str]]:
        """检测LLM输出中是否含有驱动分级标记。
        返回 (has_level_markers, bold_titles, level_content_after_colon)
        共享方法，供 _parse_drivers 和 _parse_chains 共同使用。"""
        has_levels = bool(re.search(r'(?:^|\n)\s*[-–—*#|]*\s*\**\s*[SABC]级', text))
        bold_titles = re.findall(r'\*\*([^*]+)\*\*', text)
        # 过滤掉纯"S级"/"A级"等分级标记
        bold_titles = [t for t in bold_titles if not re.match(r'^[SABC]级$', t.strip())]
        level_contents = re.findall(r'[SABC]级\**\s*[：:]\s*([^\n]{5,40})', text)
        return has_levels, bold_titles, level_contents


if __name__ == "__main__":
    agent = NewsAgent()
    result = agent.run()
    if result["success"]:
        plog("INFO", f"✅ 新闻分析完成 | 来源: {result['source']}")
        plog("INFO", f"📄 保存: {result['saved_to']}")
        plog("INFO", "\n" + "=" * 40)
        plog("INFO", result["report"][:1000])
    else:
        plog("INFO", f"❌ 失败: {result.get('error')}")
