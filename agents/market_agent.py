#!/usr/bin/env python3
"""
Market Agent - 行情数据 Agent
0次LLM调用，纯API获取 + 规则计算

数据源：
  - 腾讯行情 API（实时快照，无需key）
  - 新浪财经 API（日K历史，无需key）
  - 东方财富 API（公告/新闻，无需key）
"""

import re
import subprocess
import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from logger import plog


# =============================================================================
# 新增工具：历史K线（新浪财经，无需Key）
# =============================================================================

def fetch_history(
    symbol: str,
    period: str = "day",
    num: int = 30,
    adjust: str = "qfq",
) -> list[dict]:
    """
    获取股票历史K线数据（新浪财经免费接口）

    参数:
        symbol:  股票代码，如 "sh600031" / "sz000001"
        period:  "day"日K | "week"周K | "month"月K
                  分钟线: "5" "15" "30" "60"（5分钟/15分钟/30分钟/60分钟）
        num:     返回数据根数，默认30，最大1023
        adjust:  "qfq"前复权 | "hfq"后复权 | ""不复权

    返回:
        list[dict], 每项含: date / open / high / low / close / volume
        失败返回空列表
    """
    period_map = {"day": 240, "week": 120, "month": 30, "5": 5, "15": 15, "30": 30, "60": 60}
    scale = period_map.get(period, 240)

    url = (
        f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
        f"/CN_MarketData.getKLineData"
        f"?symbol={symbol}&scale={scale}&ma=no&datalen={min(num, 1023)}"
    )

    try:
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
    except Exception:
        return []

    result = []
    for item in data:
        try:
            result.append({
                "日期": item.get("day", "")[:10],
                "开盘": round(float(item.get("open", 0)), 2),
                "最高": round(float(item.get("high", 0)), 2),
                "最低": round(float(item.get("low", 0)), 2),
                "收盘": round(float(item.get("close", 0)), 2),
                "成交量": int(item.get("volume", 0)),
            })
        except (ValueError, TypeError):
            continue
    return result


# =============================================================================
# 新增工具：腾讯财经日K（有今日实时数据，HTTPS可通）
# 返回: 包含今日最新K线
# =============================================================================

def fetch_tencent_history(
    symbol: str,
    num: int = 20,
) -> list[dict]:
    """
    获取股票日K线（腾讯财经，含今日盘中/收盘数据，HTTPS可通）

    参数:
        symbol: "sh600031" / "sz300342" 格式
        num:    返回根数

    返回:
        list[dict], 每项含: 日期/开盘/最高/最低/收盘/成交量
        失败返回空列表
    """
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?_var=kline_dayqfq&param={symbol},day,,,{num},qfq"
    )
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
        raw = re.sub(r"^[^=]+=", "", raw)
        data = json.loads(raw)
        stock_data = list(data.get("data", {}).values())[0]
        klines = stock_data.get("qfqday") or stock_data.get("day", [])
    except Exception:
        return []

    result = []
    for k in klines:
        try:
            result.append({
                "日期":    k[0][:10],
                "开盘":    round(float(k[1]), 2),
                "最高":    round(float(k[2]), 2),
                "最低":    round(float(k[3]), 2),
                "收盘":    round(float(k[4]), 2),
                "成交量":  int(float(k[5])),
            })
        except (ValueError, IndexError):
            continue
    return result


# =============================================================================
# 新增工具：东方财富历史K线（有今日实时数据，无需Key）
# secid格式: 1.600031(沪) / 0.300342(深)
# 返回: 今日数据、最新5日K均有
# =============================================================================

def fetch_em_history(
    symbol: str,
    num: int = 20,
) -> list[dict]:
    """
    获取股票历史K线（东方财富，含今日实时数据）

    参数:
        symbol: "sh600031" / "sz300342" 格式
        num:    返回根数（每根=1日K）

    返回:
        list[dict], 每项含: 日期/开盘/最高/最低/收盘/成交量
        失败返回空列表
    """
    # 转东方财富 secid
    raw = symbol[2:] if symbol[:2] in ('sh', 'sz') else symbol
    prefix = '1' if raw.startswith(('6', '5', '9')) else '0'
    secid = f"{prefix}.{raw}"

    end = datetime.now().strftime('%Y%m%d')
    beg = (datetime.now() - timedelta(days=num * 2)).strftime('%Y%m%d')

    url = (
        f"http://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt=101&fqt=1&beg={beg}&end={end}&smplmt={num}&lmt={num}"
    )

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "http://finance.eastmoney.com"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_data = json.loads(resp.read())
        klines = raw_data.get('data', {}).get('klines', [])
    except Exception:
        return []

    result = []
    for line in klines:
        try:
            parts = line.split(',')
            result.append({
                "日期":    parts[0][:10],
                "开盘":    round(float(parts[1]), 2),
                "最高":    round(float(parts[2]), 2),
                "最低":    round(float(parts[3]), 2),
                "收盘":    round(float(parts[4]), 2),
                "成交量":  int(parts[5]),
            })
        except (ValueError, IndexError):
            continue
    return result


def fmt_history(hist: list[dict], limit: int = 20) -> str:
    """
    将历史K线格式化为紧凑Markdown表格（Token压缩，类比Dexter formatters）
    原始JSON 5-10KB → Markdown ~500B
    """
    if not hist:
        return "（无历史数据）"

    # 取最近N条（hist已按时间正序）
    rows = hist[-limit:]
    # 计算涨跌幅
    lines = ["| 日期 | 开 | 高 | 低 | 收 | 涨跌% | 量(K) |"]
    lines.append("|------|----|----|----|----|-------|------|")
    prev_close = None
    for r in rows:
        close = r["收盘"]
        chg = ""
        if prev_close is not None and prev_close > 0:
            pct = (close - prev_close) / prev_close * 100
            chg = f"{pct:+.2f}%"
        vol_k = r["成交量"] // 1000
        lines.append(
            f"| {r['日期']} | {r['开盘']} | {r['最高']} | {r['最低']} "
            f"| {close} | {chg} | {vol_k} |"
        )
        prev_close = close
    return "\n".join(lines)


# =============================================================================
# 新增工具：公告/新闻（东方财富，无需Key）
# =============================================================================

def fetch_news(symbol: str, max_items: int = 10) -> list[dict]:
    """
    获取个股最新公告（东方财富免费接口 + 客户端精准过滤）

    参数:
        symbol:  股票代码如 "600031" 或公司名如 "三一重工"
        max_items: 最大返回条数

    返回:
        list[dict], 每项含: 标题 / 日期 / 类型 / 链接
        优先返回含股票代码/公司名的公告；不足则补充市场最新公告
    """
    code = symbol.strip().upper()
    if len(code) == 6 and code.isdigit():
        ann_type = "SHA" if code.startswith(("6", "5", "9")) else "SZA"
        keywords = [code]  # 精准: 股票代码
    else:
        ann_type = "SHA"
        keywords = [symbol]  # 公司名作为关键词

    url = (
        "https://np-anotice-stock.eastmoney.com/api/security/ann"
        "?sr=-1&page_size=20&page_index=1"
        f"&ann_type={ann_type}&client_source=web"
    )

    try:
        req = urllib.request.Request(
            url,
            headers={
                "Referer": "https://finance.eastmoney.com/",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        items = data.get("data", {}).get("list", [])
    except Exception:
        return []

    # 客户端精准过滤：标题含股票代码
    matched, others = [], []
    for item in items:
        try:
            title = item.get("title", "")
            notice_date = item.get("notice_date", "")[:10]
            # 东方财富公告标题格式: "公司名:公告标题"，提取公司代码段
            entry = {
                "标题": title.strip(),
                "日期": notice_date,
                "类型": item.get("notice_type", ""),
                "art_id": item.get("art_id", ""),
                "链接": f"https://np-anotice-stock.eastmoney.com/#?artId={item.get('art_id', '')}",
            }
            # 精准匹配：标题中含股票代码
            if any(kw in title for kw in keywords):
                matched.append(entry)
            else:
                others.append(entry)
        except (AttributeError, TypeError):
            continue

    # 优先返回精准匹配，不够再补充市场公告
    result = matched + others
    return result[:max_items]


def fmt_news(news: list[dict], limit: int = 10) -> str:
    """
    将公告列表格式化为紧凑Markdown列表（Token压缩）
    """
    if not news:
        return "（无公告数据）"
    lines = []
    for i, n in enumerate(news[:limit], 1):
        lines.append(f"{i}. **{n['标题']}**")
        lines.append(f"   📅 {n['日期']} | 📋 {n['类型']}")
    return "\n".join(lines)


# =============================================================================
# 原有函数
# =============================================================================

def validate_stock_codes(codes: list[str]) -> list[str]:
    """
    验证股票代码是否有效
    返回有效的代码列表
    """
    if not codes:
        return []
    
    # 转换为腾讯格式
    tx_codes = []
    for code in codes:
        code = code.strip().upper()
        # 去掉.SH/.SZ后缀
        code = code.replace(".SH", "").replace(".SZ", "").replace("SH", "").replace("SZ", "")
        if len(code) == 6 and code.isdigit():
            market = "sh" if code.startswith(("6", "5", "9")) else "sz"
            tx_codes.append(f"{market}{code}")
    
    if not tx_codes:
        return []
    
    query = ",".join(tx_codes)
    url = f"https://qt.gtimg.cn/q={query}"
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://finance.qq.com", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("gbk", errors="replace").strip()
    except Exception:
        return []
    
    # 验证：排除"v_pv_none_match"
    valid = []
    lines = [l for l in content.split("\n") if l.strip()]
    for line in lines:
        if "v_pv_none_match" not in line and "~" in line:
            parts = line.split("~")
            if len(parts) >= 3:
                code = parts[2].strip()
                if code and code.isdigit():
                    valid.append(code)
    
    return valid


def to_api(code: str) -> str:
    """将纯代码转为腾讯API所需的前缀格式。例: '600118' → 'sh600118', '300342' → 'sz300342'"""
    code = code.strip().upper()
    code = code.replace(".SH", "").replace(".SZ", "").replace("SH", "").replace("SZ", "")
    if len(code) == 6 and code.isdigit():
        prefix = "sh" if code.startswith(("6", "5", "9")) else "sz"
        return f"{prefix}{code}"
    return code


def fetch_quotes(codes: list[str]) -> list[dict]:
    """
    批量获取股票行情（腾讯API）
    codes: ['sh601919', 'sz300866', 'sh600118']
    
    ⚠️ 腾讯API返回 ~ 分隔的文本，字段位置硬编码如下：
        parts[1]=名称  [2]=代码  [3]=现价  [4]=昨收  [5]=今开
        [31]=涨跌额 [32]=涨跌幅 [33]=最高 [34]=最低
        [36]=成交量(手) [37]=成交额(万) [38]=换手率% [39]=市盈率
        [43]=振幅% [44]=流通市值(亿) [45]=总市值(亿)
        [49]=量比 [50]=委托买入 [51]=委托卖出
        [52]=内盘 [53]=外盘 [56]=每股净资产
        [63]=月涨跌 [64]=季涨跌 [65]=年涨跌
    如腾讯API变更格式，需更新此映射及对应索引。
    """
    if not codes:
        return []

    query = ",".join(codes)
    url = f"https://qt.gtimg.cn/q={query}"
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://finance.qq.com", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("gbk", errors="replace").strip()
    except Exception:
        return []

    results = []
    lines = [l for l in content.split("\n") if l.strip()]

    for line in lines:
        parts = line.split("~")
        if len(parts) < 40:
            continue

        # 判断市场：sh=上海，sz=深圳
        raw_code = parts[0].replace('v_', '').replace('"', '').strip()
        market = "SH" if raw_code.startswith("sh") else "SZ" if raw_code.startswith("sz") else "?"
        code = parts[2].strip()

        try:
            if len(parts) <= 65:
                continue  # 字段不足，跳过该条
            price = float(parts[3])
            prev_close = float(parts[4])
            # P2-1：涨幅提取精度增强
            change = float(parts[31])
            change_pct = float(parts[32])
            # 交叉验证：用 (现价-昨收)/昨收 重新计算涨跌幅，避免API原始数据误差
            calculated_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
            # 如果API涨跌幅与计算值偏差>0.1%，以计算值为准（更精确）
            if calculated_pct and abs(calculated_pct - change_pct) > 0.1:
                change_pct = calculated_pct
                change = round(price - prev_close, 2)
            high = float(parts[33])
            low = float(parts[34])
            volume = int(parts[36])          # 成交量(手)
            amount = float(parts[37])         # 成交额(万)
            turnover = float(parts[38])       # 换手率%
            pe = float(parts[39]) if parts[39] else 0  # 市盈率
            amplitude = float(parts[43])      # 振幅%
            mkt_cap = float(parts[44])        # 流通市值(亿)
            total_cap = float(parts[45])      # 总市值(亿)
            # 判断交易状态(涨停/跌停)：按ST→创业板/科创板→北交所→主板的优先级覆盖
            stock_name = parts[1]  # 股票名称，用于ST判断
            is_st = 'ST' in stock_name or '*ST' in stock_name
            if is_st:
                upper_limit = prev_close * 1.05  # ST股5%涨停
                lower_limit = prev_close * 0.95
            elif raw_code.startswith(('3', '68')):
                upper_limit = prev_close * 1.2   # 创业板/科创板20%
                lower_limit = prev_close * 0.8
            elif raw_code.startswith(('43', '83', '87')):
                upper_limit = prev_close * 1.3   # 北交所30%
                lower_limit = prev_close * 0.7
            else:
                upper_limit = prev_close * 1.1   # 主板10%
                lower_limit = prev_close * 0.9
            # 判断当前价格是否封板（允许千分之一精度误差）
            if price >= upper_limit * 0.999:
                trade_status = "涨停"
            elif price <= lower_limit * 1.001:
                trade_status = "跌停"
            else:
                trade_status = "正常"
            vol_ratio = float(parts[49])      # 量比
            month_chg = float(parts[63]) if parts[63] else 0   # 月涨跌%
            quarter_chg = float(parts[64]) if parts[64] else 0  # 季涨跌%
            year_chg = float(parts[65]) if parts[65] else 0   # 年涨跌%

            results.append({
                "代码": code,
                "市场": market,
                "名称": parts[1],
                "现价": price,
                "昨收": prev_close,
                "今开": float(parts[5]),
                "最高": high,
                "最低": low,
                "涨跌额": change,
                "涨跌幅": change_pct,
                "成交量": volume,
                "成交额_万": amount,
                "换手率": turnover,
                "市盈率_TTM": pe,
                "振幅": amplitude,
                # 市净率: 腾讯API未直接提供此字段，留空待接入
                "市净率": 0,
                "流通市值_亿": mkt_cap,
                "总市值_亿": total_cap,
                "涨停价": upper_limit,
                "跌停价": lower_limit,
                "交易状态": trade_status,
                "量比": vol_ratio,
                "委托买入": float(parts[50]) if parts[50] else 0,
                "委托卖出": float(parts[51]) if parts[51] else 0,
                "内盘_手": float(parts[52]) if parts[52] else 0,
                "外盘_手": float(parts[53]) if parts[53] else 0,
                "每股净资产": float(parts[56]) if parts[56] else 0,
                "月涨跌": month_chg,
                "季涨跌": quarter_chg,
                "年涨跌": year_chg,
                # 52周极值: 腾讯API未直接提供，需从历史K线计算
                "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
        except (ValueError, IndexError):
            continue

    return results


def calculate_technical_score(stock: dict) -> dict:
    """
    基于行情数据计算技术面评分（规则，无LLM）
    """
    score = 50  # 基准分
    reasons = []

    # 1. 趋势判断（20分）
    chg = stock.get("涨跌幅", 0)
    if chg > 5:
        score += 10
        reasons.append(f"强势上涨({chg:+.2f}%)")
    elif chg > 2:
        score += 5
        reasons.append(f"温和上涨({chg:+.2f}%)")
    elif chg < -3:
        score -= 10
        reasons.append(f"下跌偏弱({chg:+.2f}%)")

    # 2. 量能判断（20分）
    vol_ratio = stock.get("量比", 1)
    turnover = stock.get("换手率", 0)
    if vol_ratio > 2:
        score += 10
        reasons.append(f"放量({vol_ratio:.2f}x)")
    elif vol_ratio > 1.5:
        score += 5
        reasons.append(f"温和放量({vol_ratio:.2f}x)")
    elif vol_ratio < 0.5:
        score -= 5
        reasons.append("缩量低迷")

    if turnover > 10:
        score += 10
        reasons.append(f"高换手({turnover:.1f}%)")
    elif turnover > 5:
        score += 5
        reasons.append(f"适中换手({turnover:.1f}%)")

    # 3. 位置分析（20分）
    price = stock.get("现价", 0)
    high = stock.get("最高", 0)
    low = stock.get("最低", 0)
    upper = stock.get("涨停价", 0)
    lower = stock.get("跌停价", 0)

    if upper > 0 and price >= upper * 0.98:
        score -= 15
        reasons.append("接近涨停，追高风险大")
    elif high > 0 and low > 0:
        position_ratio = (price - low) / (high - low) if high != low else 0.5
        if position_ratio > 0.85:
            score += 8
            reasons.append("价格接近日内高位")
        elif position_ratio < 0.15:
            score += 5
            reasons.append("价格接近日内低位，有反弹可能")
        elif position_ratio > 0.5:
            score += 3
            reasons.append("日内偏强")

    # 4. 风险扫描（20分）
    mkt_cap = stock.get("流通市值_亿", 0)
    if mkt_cap < 5:
        score -= 10
        reasons.append("小盘股，流动性风险")
    elif mkt_cap > 500:
        score += 5
        reasons.append("大盘蓝筹，流动性好")

    amplitude = stock.get("振幅", 0)
    if amplitude > 10:
        score -= 5
        reasons.append(f"振幅过大({amplitude:.1f}%)")

    # 5. 短期动能（20分）
    month_chg = stock.get("月涨跌", 0)
    if month_chg > 20:
        score -= 10
        reasons.append(f"月涨幅过大({month_chg:.1f}%)，注意回调")
    elif month_chg > 10:
        score -= 5
        reasons.append(f"月涨幅较大({month_chg:.1f}%)")
    elif month_chg > 0:
        score += 5
        reasons.append(f"月线收红({month_chg:.1f}%)")

    # 边界修正
    score = max(0, min(100, score))

    return {
        "技术面评分": score,
        "评分理由": reasons if reasons else ["数据不足"],
        "风险提示": [],
    }


def calculate_qlib_factors(stock: dict) -> dict:
    """
    计算 Qlib 经典因子（规则，0次LLM）。
    基于30日历史K线，输出6个因子值 + 综合信号强度。
    失败时返回空因子不抛异常。
    """
    import numpy as np
    from market_agent import fetch_tencent_history, to_api

    code = str(stock.get("代码") or stock.get("股票代码", stock.get("symbol", ""))).strip()
    name = stock.get("名称", stock.get("股票名称", code))
    if not code:
        return {"factor_signal": 0, "factor_details": {}}

    try:
        api_code = to_api(code)
        hist = fetch_tencent_history(api_code, num=30)
        if not hist or len(hist) < 8:
            return {"factor_signal": 0, "factor_details": {}}

        closes = np.array([d.get("close", d.get("收盘", 0)) for d in hist], dtype=float)[::-1]
        volumes = np.array([d.get("volume", d.get("成交量", 0)) for d in hist], dtype=float)[::-1]

        # ── 6个Qlib经典因子 ─────────────────────────────
        # 1. MA5乖离率: 5日均线/收盘价 (momentum)
        ma5 = round(float(np.mean(closes[-5:]) / closes[-1]), 4) if len(closes) >= 5 else 0
        # 2. MA10乖离率: 10日均线/收盘价 (momentum)
        ma10 = round(float(np.mean(closes[-10:]) / closes[-1]), 4) if len(closes) >= 10 else 0
        # 3. RET5: 5日涨幅 (reversal)
        ret5 = round(float(closes[-1] / closes[-5] - 1), 4) if len(closes) >= 5 else 0
        # 4. RET20: 20日涨幅 (trend)
        ret20 = round(float(closes[-1] / closes[-20] - 1), 4) if len(closes) >= 20 else 0
        # 5. VOL20: 20日波动率 (risk)
        vol20 = round(float(np.std(closes[-20:] / closes[-21:-1]) * np.sqrt(242)), 2) if len(closes) >= 21 else 0
        # 6. TURN: 当日量比 (volume)
        turn = round(float(volumes[-1] / (np.mean(volumes[-5:]) + 1)), 2) if len(volumes) >= 5 else 0

        # ── 综合信号打分（6因子，各1分）─────────────────
        signals = sum([
            ma5 > 1.02,       # 短期均线趋势向上
            ret5 > 0.02,      # 5日正收益
            turn > 1.2,       # 放量
            vol20 < 0.3,      # 低波动
            ret20 > 0.03,     # 中期趋势向上
            ma10 > 1.02,      # 中期均线向上
        ])

        factors = {
            "factor_ma5": ma5,
            "factor_ma10": ma10,
            "factor_ret5": ret5,
            "factor_ret20": ret20,
            "factor_vol20": vol20,
            "factor_turn": turn,
        }

        # ML额外因子: 振幅 + 相对20日线位置
        high = float(hist[-1].get("最高", closes[-1]))
        low = float(hist[-1].get("最低", closes[-1]))
        factors["day_range"] = round((high - low) / closes[-1] * 100, 2) if closes[-1] else 0
        ma20_avg = float(np.mean(closes[-20:])) if len(closes) >= 20 else closes[-1]
        factors["ma20_pos"] = round((closes[-1] - ma20_avg) / ma20_avg * 100, 2) if ma20_avg else 0

        return {
            "factor_signal": signals,        # 0-6, 综合信号
            "factor_details": factors,        # 各因子原始值
            "factor_data_days": len(closes),  # 数据充足度
        }
    except Exception:
        return {"factor_signal": 0, "factor_details": {}}


def analyze_batch(stocks: list[dict]) -> list[dict]:
    """对一批股票进行技术面分析（含因子计算）"""
    results = []
    for stock in stocks:
        tech = calculate_technical_score(stock)
        stock.update(tech)
        factors = calculate_qlib_factors(stock)
        stock.update(factors)
        results.append(stock)
    return results


def format_market_report(analyzed: list[dict], title: str = "技术面分析") -> str:
    """格式化技术面报告"""
    if not analyzed:
        return "（无行情数据）"

    lines = [f"## {title}\n"]
    lines.append(f"| 股票 | 现价 | 涨跌幅 | 技术分 | 量比 | 换手率 | 市值(亿) | 状态 |\n")
    lines.append("|------|------|--------|--------|------|--------|----------|------|\n")

    for s in analyzed:
        score = s.get("技术面评分", 0)
        score_emoji = "🟢" if score >= SCORE_BASE_HIGH else "🟡" if score >= SCORE_BASE_MED else "🔴"

        chg = s.get("涨跌幅", 0)
        chg_str = f"{chg:+.2f}%" if chg != 0 else "N/A"

        status = "强势" if chg > 2 else "偏强" if chg > 0 else "偏弱" if chg > -3 else "弱势"

        lines.append(
            f"| {s.get('名称','?')}({s.get('代码','?')}) "
            f"| {s.get('现价','?')} "
            f"| {chg_str} "
            f"| {score_emoji}{score} "
            f"| {s.get('量比','?')} "
            f"| {s.get('换手率','?'):.1f}% "
            f"| {s.get('流通市值_亿','?'):.1f} "
            f"| {status} |\n"
        )

    return "".join(lines)


class MarketAgent:
    """行情分析 Agent"""

    def __init__(self, root=None):
        from pathlib import Path as _Path
        self.root = root or _Path(__file__).parent.parent.resolve()
        self.history_dir = self.root / "data" / "历史记录"
        self.pool_dir = self.root / "五池管理"

    def _codes_from_pools(self) -> list[str]:
        """从五池获取所有股票代码"""
        codes = []
        for name in ["快筛候选池", "重点观察池", "持仓池"]:
            f = self.pool_dir / f"{name}.json"
            if f.exists():
                data = json.loads(f.read_text(encoding="utf-8"))
                objs = data.get("stocks", [])
                for obj in objs:
                    code = obj.get("股票代码", obj.get("代码", ""))
                    if code and code != "000000":
                        market = "sh" if code.startswith(("6", "5", "9")) else "sz"
                        codes.append(f"{market}{code}")
        # 追加大盘指数，供决策分析使用
        codes.append("sh000001")  # 上证指数
        codes.append("sz399001")  # 深证成指
        codes.append("sz399006")  # 创业板指
        return list(dict.fromkeys(codes))  # 去重

    def run(self, codes: Optional[list[str]] = None) -> dict:
        """
        执行行情分析
        codes: 可选指定代码列表，如 ['sh601919', 'sz300866']
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # 如果没指定，从五池获取
        if codes is None:
            codes = self._codes_from_pools()

        if not codes:
            return {"success": False, "error": "没有可分析的股票"}

        # 批量获取行情
        quotes = fetch_quotes(codes)
        if not quotes:
            return {"success": False, "error": "行情API无响应"}

        # 技术面评分
        analyzed = analyze_batch(quotes)

        # 按评分排序
        analyzed.sort(key=lambda x: x.get("技术面评分", 0), reverse=True)

        # 格式化报告
        report = f"""# 【技术面分析】{today}

## 行情数据概览

{format_market_report(analyzed)}

## 重点关注

"""
        # 找出技术面评分最高的3只
        strong = [s for s in analyzed if s.get("技术面评分", 0) >= 60]
        if strong:
            for s in strong[:3]:
                reasons = s.get("评分理由", [])
                report += f"### {s['名称']}({s['代码']})\n"
                report += f"- 现价：{s['现价']} | 涨跌幅：{s['涨跌幅']:+.2f}%\n"
                report += f"- 技术评分：{s['技术面评分']}分\n"
                for r in reasons[:3]:
                    report += f"- {r}\n"
                report += "\n"
        else:
            report += "（当前无技术面强势标的）\n"

        report += f"---\n行情时间：{datetime.now().strftime('%H:%M')}\n"

        # 保存
        self.history_dir.mkdir(parents=True, exist_ok=True)
        out_file = self.history_dir / f"{today}_技术面分析.md"
        out_file.write_text(report, encoding="utf-8")

        # 保存到共享内存
        shared = self.root / "data" / "shared_memory.json"
        shared.write_text(json.dumps(analyzed, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "success": True,
            "report": report,
            "analyzed": analyzed,
            "saved_to": str(out_file),
            "count": len(analyzed),
        }

    def run_history(self, symbol: str, period: str = "day", num: int = 30) -> dict:
        """
        获取个股历史K线（新浪财经免费API）
        symbol: 股票代码，如 "sh600031"
        period: "day"日K | "week"周K | "month"月K | "5"/"15"/"30"/"60" 分钟线
        num: 数据根数，默认30，最大1023
        """
        today = datetime.now().strftime("%Y-%m-%d")
        hist = fetch_history(symbol, period=period, num=num)
        if not hist:
            return {"success": False, "error": f"历史K线获取失败: {symbol}"}

        # 保存
        self.history_dir.mkdir(parents=True, exist_ok=True)
        out_file = self.history_dir / f"{today}_{symbol}_历史K线.md"
        content = f"# 【历史K线】{symbol} ({period.upper()})\n\n"
        content += f"最近 {len(hist)} 个交易日\n\n"
        content += fmt_history(hist) + "\n"
        content += f"\n---\n生成时间：{datetime.now().strftime('%H:%M:%S')}\n"
        out_file.write_text(content, encoding="utf-8")

        return {
            "success": True,
            "symbol": symbol,
            "period": period,
            "count": len(hist),
            "saved_to": str(out_file),
            "latest": hist[-1] if hist else {},
            "markdown": fmt_history(hist),
        }

    def run_news(self, symbol: str, max_items: int = 10) -> dict:
        """
        获取个股最新公告（东方财富免费API）
        symbol: 股票代码，如 "600031"（纯数字，自动判断沪/深）
        """
        today = datetime.now().strftime("%Y-%m-%d")
        news = fetch_news(symbol, max_items=max_items)
        if not news:
            return {"success": False, "error": f"公告获取失败: {symbol}"}

        # 保存
        self.history_dir.mkdir(parents=True, exist_ok=True)
        out_file = self.history_dir / f"{today}_{symbol}_公告.md"
        content = f"# 【最新公告】{symbol}\n\n"
        content += fmt_news(news, limit=max_items) + "\n"
        content += f"\n---\n生成时间：{datetime.now().strftime('%H:%M:%S')}\n"
        out_file.write_text(content, encoding="utf-8")

        return {
            "success": True,
            "symbol": symbol,
            "count": len(news),
            "saved_to": str(out_file),
            "markdown": fmt_news(news),
        }


if __name__ == "__main__":
    import sys
    from pathlib import Path
    _ROOT = Path(__file__).parent.parent.resolve()
    sys.path.insert(0, str(_ROOT / "agents"))
    agent = MarketAgent(root=_ROOT)

    # 演示：获取三一重工(600031) 三个维度数据
    plog("INFO", "=" * 55)
    plog("INFO", "📊 MarketAgent 演示 — 三一重工(600031)")
    plog("INFO", "=" * 55)

    # 1. 实时行情（腾讯API，已有）
    plog("INFO", "\n🔴 [1] 实时行情（腾讯API）...")
    r1 = agent.run(codes=["sh600031"])
    if r1["success"]:
        plog("INFO", f"   ✅ 获取 {r1['count']} 只，保存至 {r1['saved_to']}")
    else:
        plog("INFO", f"   ⚠️  {r1.get('error')}")

    # 2. 日K历史（新浪财经，新增）
    plog("INFO", "\n📈 [2] 日K历史（新浪财经）...")
    r2 = agent.run_history("sh600031", period="day", num=20)
    if r2["success"]:
        plog("INFO", f"   ✅ 获取 {r2['count']} 条，保存至 {r2['saved_to']}")
        plog("INFO", f"   最新收盘: {r2['latest'].get('收盘')} 元")
    else:
        plog("INFO", f"   ⚠️  {r2.get('error')}")

    # 3. 最新公告（东方财富，新增）
    plog("INFO", "\n📋 [3] 最新公告（东方财富）...")
    r3 = agent.run_news("600031", max_items=5)
    if r3["success"]:
        plog("INFO", f"   ✅ 获取 {r3['count']} 条，保存至 {r3['saved_to']}")
    else:
        plog("INFO", f"   ⚠️  {r3.get('error')}")

    plog("INFO", "\n" + "=" * 55)
