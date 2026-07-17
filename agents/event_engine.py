#!/usr/bin/env python3
"""天枢事件驱动策略引擎 — 核心数据结构与框架

ED-001: 引擎框架 + 数据结构
ED-002: 数据源层封装
ED-008: 事件评分计算引擎
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field, asdict
from urllib.request import Request, urlopen
from safe_file_utils import safe_read_json

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

from logger import plog

# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class EventRecord:
    """单个事件记录"""
    event_id: str                     # 唯一ID: EV01_601398_20260716
    event_type: str                   # EV-01 ~ EV-14
    event_name: str                   # 事件中文名
    code: str                         # 股票代码
    name: str                         # 股票名称
    trigger_date: str                 # YYYY-MM-DD
    event_score: float = 0.0          # 综合评分(0-100)
    event_strength: float = 0.0       # 事件强度分(0-100)
    fundamental_score: float = 0.0    # 基本面分(0-100)
    technical_score: float = 0.0      # 技术面分(0-100)
    market_score: float = 0.0         # 市场环境分(0-100)
    decay_days: int = 5               # 有效衰减天数
    signals: Dict[str, Any] = field(default_factory=dict)
    factors: Dict[str, float] = field(default_factory=dict)
    raw_data: Dict[str, Any] = field(default_factory=dict)
    status: str = "active"            # active / expired / traded / skipped


@dataclass
class EventConfig:
    """事件策略配置"""
    event_type: str                   # 事件类型代码
    enabled: bool = True
    min_score: float = 60.0           # 最低触发评分
    max_position_pct: float = 5.0     # 最大仓位
    stop_loss_pct: float = -5.0       # 止损
    take_profit_pct: float = 12.0     # 止盈
    hold_days: int = 5                # 持仓天数
    entry_timing: str = "next_open"   # 入场时机
    require_volume_confirm: bool = True
    decay_days: int = 5               # 事件衰减周期


@dataclass
class BacktestResult:
    """单事件回测结果"""
    event_type: str
    total_signals: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_return_pct: float = 0.0
    avg_return_pct: float = 0.0
    max_return_pct: float = 0.0
    min_return_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    decay_analysis: Dict[int, float] = field(default_factory=dict)  # {t日后收益}


# ═══════════════════════════════════════════════════════════════
# 数据源层 (ED-002)
# ═══════════════════════════════════════════════════════════════

class DataSource:
    """数据源层 — 统一封装东方财富/Sina/腾讯API"""

    EASTMONEY_URL = ("https://82.push2.eastmoney.com/api/qt/clist/get?"
                     "pn={page}&pz={size}&po=1&np=1"
                     "&ut=bd1d9ddb04089700cf9c27f6f7426281"
                     "&fltt=2&invt=2&fid=f20"
                     "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
                     "&fields={fields}")

    SINA_KLINE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                  "CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={days}")

    TENCENT_QUOTE = "https://qt.gtimg.cn/q={prefix}{code}"

    # 东方财富行情字段
    BASE_FIELDS = "f12,f14,f37,f40,f41,f45,f46,f100,f20,f25,f9,f23,f3,f38,f48,f115,f152"
    # f12=代码 f14=名称 f37=ROE% f40=营收 f41=营收同比% f45=净利润 f46=净利同比%
    # f100=行业 f20=总市值 f25=涨跌幅% f9=PE f23=PB f3=最新价 f38=换手率
    # f48=净利环比% f115=每股未分配利润 f152=每股公积金

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._cache_time: Dict[str, float] = {}
        self._cache_ttl = 300  # 5分钟

    def _fetch_json(self, url: str, timeout: int = 15) -> Optional[dict]:
        """带缓存的JSON请求"""
        if url in self._cache:
            age = time.time() - self._cache_time.get(url, 0)
            if age < self._cache_ttl:
                return self._cache[url]
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
            })
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            data = json.loads(raw.decode('utf-8', errors='replace'))
            self._cache[url] = data
            self._cache_time[url] = time.time()
            return data
        except Exception:
            return None

    def _fetch_text(self, url: str, encoding: str = 'gbk') -> Optional[str]:
        """获取文本数据"""
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
            })
            raw = urllib.request.urlopen(req, timeout=15).read()
            return raw.decode(encoding, errors='replace')
        except Exception:
            return None

    def get_all_stocks(self, fields: str = None) -> List[Dict[str, Any]]:
        """获取全A股行情+财务数据（自动分页，含curl降级）"""
        all_items = []
        f = fields or self.BASE_FIELDS
        for page in range(1, 60):
            url = self.EASTMONEY_URL.format(page=page, size=500, fields=f)
            data = self._fetch_json(url)
            # curl降级（如果urllib失败）
            if not data:
                import subprocess
                try:
                    raw = subprocess.check_output(
                        ['curl', '-s', '--noproxy', '*', '-H', 'User-Agent: Mozilla/5.0', url],
                        timeout=15)
                    data = json.loads(raw.decode('utf-8'))
                except Exception:
                    break
            if not data:
                break
            items = data.get('data', {}).get('diff', [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < 500:
                break
            time.sleep(0.3)
        return all_items

    def get_kline(self, code: str, days: int = 200) -> List[Dict]:
        """获取个股K线数据（Sina API）"""
        prefix = 'sh' if code.startswith('6') else 'sz'
        url = self.SINA_KLINE.format(prefix=prefix, code=code, days=days)
        data = self._fetch_json(url)
        if not data:
            # 重试一次
            time.sleep(1.2)
            data = self._fetch_json(url)
        return data if isinstance(data, list) else []

    def get_quote(self, code: str) -> Optional[Dict[str, Any]]:
        """获取腾讯实时行情"""
        prefix = 'sh' if code.startswith('6') else 'sz'
        url = self.TENCENT_QUOTE.format(prefix=prefix, code=code)
        text = self._fetch_text(url)
        if not text:
            return None
        return self._parse_tencent_quote(text)

    def _parse_tencent_quote(self, text: str) -> Dict[str, Any]:
        """解析腾讯行情~分隔字段"""
        # 格式: v_sh601398="1~工商银行~...~..."
        try:
            parts = text.split('~')
            if len(parts) < 60:
                return {}
            return {
                "name": parts[1],
                "code": parts[2],
                "price": float(parts[3]) if parts[3] else 0,
                "chg_pct": float(parts[32]) if parts[32] else 0,
                "volume": int(parts[6]) if parts[6] else 0,
                "amount": int(parts[37]) if parts[37] else 0,
                "turnover": float(parts[38]) if parts[38] else 0,
                "pe": float(parts[39]) if parts[39] else 0,
                "amplitude": float(parts[43]) if parts[43] else 0,
                "vol_ratio": float(parts[49]) if parts[49] else 0,
                "month_chg": float(parts[63]) if parts[63] else 0,
                "quarter_chg": float(parts[64]) if parts[64] else 0,
            }
        except (ValueError, IndexError):
            return {}

    def calc_ma(self, closes: List[float], period: int) -> float:
        """计算均线"""
        if len(closes) < period:
            return 0
        return sum(closes[-period:]) / period

    def parse_eastmoney_item(self, item: dict) -> Dict[str, Any]:
        """解析东方财富返回条目为标准格式"""
        def sf(v, default=0):
            try:
                return float(v) if v not in (None, '-', '', 'None') else default
            except (ValueError, TypeError):
                return default
        return {
            "code": str(item.get("f12", "")),
            "name": str(item.get("f14", "")),
            "industry": str(item.get("f100", "")),
            "price": sf(item.get("f3")),
            "chg_pct": sf(item.get("f25")),
            "market_cap": sf(item.get("f20")),
            "pe": sf(item.get("f9")),
            "pb": sf(item.get("f23")),
            "roe": sf(item.get("f37")),
            "revenue": sf(item.get("f40")),
            "rev_yoy": sf(item.get("f41")),
            "net_profit": sf(item.get("f45")),
            "np_yoy": sf(item.get("f46")),
            "np_qoq": sf(item.get("f48")),
            "turnover": sf(item.get("f38")),
            "undistributed_profit": sf(item.get("f115")),  # 每股未分配利润
            "capital_reserve": sf(item.get("f152")),        # 每股公积金
        }


# ═══════════════════════════════════════════════════════════════
# 事件评分引擎 (ED-008)
# ═══════════════════════════════════════════════════════════════

class EventScorer:
    """事件评分计算引擎"""

    @staticmethod
    def score_earnings_surprise(stock: Dict[str, Any]) -> Tuple[float, Dict]:
        """财报超预期评分 (EV-01)"""
        signals = {}
        score = 0.0

        rev_yoy = stock.get("rev_yoy", 0)
        np_yoy = stock.get("np_yoy", 0)
        np_qoq = stock.get("np_qoq", 0)
        roe = stock.get("roe", 0)

        # 营收同比评分
        if rev_yoy > 50:
            score += 35
            signals["营收暴增"] = f"营收同比+{rev_yoy:.1f}%"
        elif rev_yoy > 30:
            score += 25
            signals["营收高增"] = f"营收同比+{rev_yoy:.1f}%"
        elif rev_yoy > 15:
            score += 15

        # 净利同比评分
        if np_yoy > 100:
            score += 35
            signals["净利暴增"] = f"净利同比+{np_yoy:.1f}%"
        elif np_yoy > 50:
            score += 25
            signals["净利高增"] = f"净利同比+{np_yoy:.1f}%"
        elif np_yoy > 20:
            score += 15

        # 净利环比评分
        if np_qoq > 50:
            score += 15
        elif np_qoq > 20:
            score += 10
        elif np_qoq > 0:
            score += 5

        # ROE评分
        if roe > 20:
            score += 15
        elif roe > 15:
            score += 10
        elif roe > 10:
            score += 5

        return min(score, 100), signals

    @staticmethod
    def score_momentum_breakout(stock: Dict[str, Any],
                                 kline: List[Dict]) -> Tuple[float, Dict]:
        """价格动量突破评分 (EV-02)"""
        signals = {}
        if not kline or len(kline) < 60:
            return 0, {"数据不足": "K线不足60日"}

        closes = [float(d.get('close', 0)) for d in kline if d.get('close')]
        volumes = [float(d.get('volume', 0)) for d in kline if d.get('volume')]
        if len(closes) < 50:
            return 0, signals

        price = closes[-1]
        ma20 = DataSource().calc_ma(closes, 20)
        ma50 = DataSource().calc_ma(closes, 50)
        ma150 = DataSource().calc_ma(closes, 150) if len(closes) >= 150 else 0
        score = 0

        # 价格>均线体系
        above_ma20 = price > ma20
        above_ma50 = price > ma50
        above_ma150 = price > ma150 if ma150 else True
        if above_ma50 and above_ma150 and above_ma20:
            score += 30
            signals["多头排列"] = f"价格>MA20/MA50/MA150"
        elif above_ma50 and above_ma20:
            score += 20

        # 均线斜率（MA20向上）
        if len(closes) > 25:
            ma20_prev = DataSource().calc_ma(closes[:-5], 20)
            if ma20 > ma20_prev * 1.01:
                score += 15
                signals["MA20上升"] = f"MA20斜率向上"

        # 成交量扩张
        if len(volumes) > 20:
            vol_10 = sum(volumes[-10:]) / 10
            vol_50 = sum(volumes[-50:]) / 50
            if vol_10 > vol_50 * 1.5:
                score += 15
                signals["成交量扩张"] = f"10日均量>50日均量×1.5"

        # 价格接近新高
        high_60 = max(closes[-60:])
        if price > high_60 * 0.95:
            score += 20
            signals["接近新高"] = f"价格{price:.2f}近60日高{high_60:.2f}"

        # 趋势强度
        ret_20 = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0
        if 5 < ret_20 < 30:  # 温和上涨
            score += 20
            signals["趋势健康"] = f"20日涨幅{ret_20:.1f}%"

        return min(score, 100), signals

    @staticmethod
    def score_volume_surge(stock: Dict[str, Any],
                            kline: List[Dict]) -> Tuple[float, Dict]:
        """成交量异常放量评分 (EV-03)"""
        signals = {}
        if not kline or len(kline) < 10:
            return 0, signals
        volumes = [float(d.get('volume', 0)) for d in kline if d.get('volume')]
        closes = [float(d.get('close', 0)) for d in kline if d.get('close')]
        if len(volumes) < 10:
            return 0, signals

        vol_today = volumes[-1]
        vol_ma5 = sum(volumes[-5:]) / 5
        vol_ma20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else vol_ma5
        chg_pct = stock.get("chg_pct", 0)
        score = 0

        # 放量倍数
        vol_ratio = vol_today / max(vol_ma5, 1)
        if vol_ratio > 3:
            score += 30
            signals["巨量"] = f"今日量/5日均量={vol_ratio:.1f}倍"
        elif vol_ratio > 2:
            score += 20
            signals["放量"] = f"今日量/5日均量={vol_ratio:.1f}倍"
        elif vol_ratio > 1.5:
            score += 10

        # 量价配合
        if chg_pct > 3 and vol_ratio > 2:
            score += 25
            signals["量价齐升"] = f"涨幅{chg_pct:.1f}%+放量{vol_ratio:.1f}倍"
        elif chg_pct > 0 and vol_ratio > 1.5:
            score += 15

        # 成交量趋势（20日均量 > 50日均量）
        if len(volumes) >= 50:
            vol_ma50 = sum(volumes[-50:]) / 50
            if vol_ma20 > vol_ma50 * 1.3:
                score += 15
                signals["活跃度提升"] = f"20日均量>50日均量×1.3"

        # 换手率辅助
        turnover = stock.get("turnover", 0)
        if 2 < turnover < 10:
            score += 15
        elif turnover > 10:
            score += 5  # 过高换手可能是出货

        return min(score, 100), signals

    @staticmethod
    def score_profit_gap(stock: Dict[str, Any],
                          kline: List[Dict]) -> Tuple[float, Dict]:
        """净利润断层评分 (EV-04)"""
        signals = {}
        np_yoy = stock.get("np_yoy", 0)
        chg_pct = stock.get("chg_pct", 0)
        score = 0

        # 净利大幅增长
        if np_yoy > 100:
            score += 35
        elif np_yoy > 50:
            score += 25
        elif np_yoy > 30:
            score += 15

        # 当日涨幅(模拟跳空)
        if chg_pct > 5:
            score += 30
            signals["跳空上涨"] = f"当日涨幅{chg_pct:.1f}%"
        elif chg_pct > 3:
            score += 20

        # K线跳空确认
        if kline and len(kline) >= 2:
            c1 = float(kline[-2].get('close', 0))
            o0 = float(kline[-1].get('open', 0))
            if o0 > c1 * 1.02:
                score += 20
                signals["跳空缺口"] = f"开盘{o0:.2f}>前收{c1:.2f}"

        # 基本面辅助
        roe = stock.get("roe", 0)
        if roe > 15:
            score += 15

        return min(score, 100), signals

    @staticmethod
    def score_oversold_rebound(stock: Dict[str, Any],
                                kline: List[Dict]) -> Tuple[float, Dict]:
        """超跌反弹评分 (EV-05)"""
        signals = {}
        if not kline or len(kline) < 20:
            return 0, signals
        closes = [float(d.get('close', 0)) for d in kline if d.get('close')]
        if len(closes) < 10:
            return 0, signals
        score = 0

        # 近期跌幅
        ret_5 = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0
        ret_10 = (closes[-1] - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 else 0
        ret_20 = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0

        if ret_5 < -15:
            score += 40
            signals["急跌"] = f"5日跌幅{ret_5:.1f}%"
        elif ret_5 < -10:
            score += 30
        elif ret_10 < -15:
            score += 25

        if ret_10 < -20:
            score += 20
        elif ret_20 < -30:
            score += 15

        # 成交量萎缩确认
        volumes = [float(d.get('volume', 0)) for d in kline if d.get('volume')]
        if len(volumes) >= 10:
            vol_recent = sum(volumes[-3:]) / 3
            vol_ma20 = sum(volumes[-20:]) / 20
            if vol_recent < vol_ma20 * 0.6:
                score += 20
                signals["缩量企稳"] = f"近3日均量<20日均量×0.6"
            elif vol_recent < vol_ma20 * 0.8:
                score += 10

        # 价格位置(远离MA50，超卖)
        if len(closes) >= 50:
            ma50 = DataSource().calc_ma(closes, 50)
            if closes[-1] < ma50 * 0.85:
                score += 20
                signals["深度超卖"] = f"价格<MA50×0.85"

        return min(score, 100), signals

    @staticmethod
    def score_high_roe_growth(stock: Dict[str, Any]) -> Tuple[float, Dict]:
        """高ROE持续增长评分 (EV-06)"""
        signals = {}
        roe = stock.get("roe", 0)
        rev_yoy = stock.get("rev_yoy", 0)
        np_yoy = stock.get("np_yoy", 0)
        score = 0

        # ROE评分
        if roe > 25:
            score += 30
            signals["高ROE"] = f"ROE{roe:.1f}%"
        elif roe > 20:
            score += 25
        elif roe > 15:
            score += 15

        # 营收持续增长
        if rev_yoy > 30:
            score += 25
        elif rev_yoy > 20:
            score += 20
        elif rev_yoy > 10:
            score += 10

        # 净利增长
        if np_yoy > 50:
            score += 25
        elif np_yoy > 30:
            score += 20
        elif np_yoy > 15:
            score += 10

        # 净利环比为正（增长持续性）
        np_qoq = stock.get("np_qoq", 0)
        if np_qoq > 20:
            score += 20
        elif np_qoq > 0:
            score += 10

        return min(score, 100), signals

    @staticmethod
    def score_high_send_expect(stock: Dict[str, Any]) -> Tuple[float, Dict]:
        """高送转预期评分 (EV-07)"""
        signals = {}
        profit = stock.get("undistributed_profit", 0)
        reserve = stock.get("capital_reserve", 0)
        np_yoy = stock.get("np_yoy", 0)
        score = 0

        # 每股未分配利润
        if profit > 3:
            score += 30
            signals["高未分配利润"] = f"每股{profit:.2f}元"
        elif profit > 2:
            score += 20
        elif profit > 1:
            score += 10

        # 每股公积金
        if reserve > 5:
            score += 30
            signals["高公积金"] = f"每股{reserve:.2f}元"
        elif reserve > 3:
            score += 20
        elif reserve > 2:
            score += 10

        # 业绩增长
        if np_yoy > 30:
            score += 20
        elif np_yoy > 15:
            score += 10

        # 市值适中（高送转偏好小盘）
        market_cap = stock.get("market_cap", 0)
        if 10 < market_cap / 1e8 < 100:
            score += 20
        elif 100 < market_cap / 1e8 < 200:
            score += 10

        return min(score, 100), signals

    @staticmethod
    def calc_composite_score(event_type: str, stock: Dict[str, Any],
                              kline: List[Dict] = None) -> Tuple[float, Dict, float, float]:
        """计算综合评分

        Returns:
            (composite_score, signals, technical_score, market_score)
        """
        scorer_map = {
            "EV-01": EventScorer.score_earnings_surprise,
            "EV-02": lambda s, k: EventScorer.score_momentum_breakout(s, k or []),
            "EV-03": lambda s, k: EventScorer.score_volume_surge(s, k or []),
            "EV-04": lambda s, k: EventScorer.score_profit_gap(s, k or []),
            "EV-05": lambda s, k: EventScorer.score_oversold_rebound(s, k or []),
            "EV-06": EventScorer.score_high_roe_growth,
            "EV-07": EventScorer.score_high_send_expect,
        }

        scorer = scorer_map.get(event_type)
        if not scorer:
            return 0, {}, 0, 0

        intensity, signals = scorer(stock, kline) if event_type in ("EV-02", "EV-03", "EV-04", "EV-05") else scorer(stock)
        tech, mkt = 0, 0

        # 技术面辅助评分（如果有K线）
        if kline and len(kline) >= 20:
            closes = [float(d.get('close', 0)) for d in kline if d.get('close')]
            if closes:
                ret_20 = (closes[-1] - closes[-20]) / closes[-20] * 100
                # 趋势加分
                if 0 < ret_20 < 25:
                    tech = 60 + (25 - ret_20)
                elif ret_20 > 25:
                    tech = 40  # 过热
                else:
                    tech = 30 + min(abs(ret_20), 20)

                # 市场环境
                chg = stock.get("chg_pct", 0)
                if -2 < chg < 5:
                    mkt = 60
                elif chg > 5:
                    mkt = 40
                else:
                    mkt = 30

        composite = intensity * 0.50 + tech * 0.25 + mkt * 0.25
        # 无技术面/市场面数据时，提升事件强度权重
        if tech == 0 and mkt == 0:
            composite = intensity * 0.80
        elif tech == 0:
            composite = intensity * 0.60 + mkt * 0.40
        return min(composite, 100), signals, tech, mkt


# ═══════════════════════════════════════════════════════════════
# 事件检测器基类
# ═══════════════════════════════════════════════════════════════

class EventDetector:
    """事件检测器基类"""

    def __init__(self, event_type: str, event_name: str, data_source: DataSource):
        self.event_type = event_type
        self.event_name = event_name
        self.ds = data_source

    def detect(self, stocks: List[Dict[str, Any]]) -> List[EventRecord]:
        """检测事件，返回触发的事件列表（子类实现）"""
        raise NotImplementedError


class EarningsSurpriseDetector(EventDetector):
    """EV-01 财报业绩超预期检测器"""

    def __init__(self, ds: DataSource):
        super().__init__("EV-01", "财报业绩超预期", ds)

    def detect(self, stocks: List[Dict[str, Any]]) -> List[EventRecord]:
        events = []
        cutoff_date = datetime.now().strftime("%Y-%m-%d")
        for item in stocks:
            stock = self.ds.parse_eastmoney_item(item)
            rev_yoy = stock.get("rev_yoy", 0)
            np_yoy = stock.get("np_yoy", 0)
            if rev_yoy < 30 or np_yoy < 50:
                continue
            score, signals, tech, mkt = EventScorer.calc_composite_score("EV-01", stock)
            if score < 60:
                continue
            event_id = f"EV01_{stock['code']}_{cutoff_date.replace('-','')}"
            events.append(EventRecord(
                event_id=event_id, event_type="EV-01",
                event_name=self.event_name,
                code=stock["code"], name=stock["name"],
                trigger_date=cutoff_date, event_score=score,
                event_strength=score, signals=signals,
                raw_data={k: stock.get(k) for k in ["rev_yoy","np_yoy","np_qoq","roe","pe","pb"]},
            ))
        return events


class CombinedEventEngine:
    """事件驱动策略引擎核心"""

    def __init__(self):
        self.ds = DataSource()
        self.detectors: List[EventDetector] = []
        self.events: List[EventRecord] = []
        self.EVENT_DIR = PROJECT_ROOT / "data" / "events"
        self.EVENT_DIR.mkdir(parents=True, exist_ok=True)

    def register_detector(self, detector: EventDetector) -> None:
        """注册事件检测器"""
        self.detectors.append(detector)

    def scan_all(self) -> List[EventRecord]:
        """全量扫描所有已注册事件检测器"""
        all_events = []
        print("📡 扫描全A股事件...")
        stocks = self.ds.get_all_stocks()
        print(f"  获取 {len(stocks)} 只股票数据")
        for detector in self.detectors:
            try:
                events = detector.detect(stocks)
                print(f"  {detector.event_name}: {len(events)} 条事件")
                all_events.extend(events)
            except Exception as e:
                print(f"  ❌ {detector.event_name} 检测失败: {e}")

        # 按评分排序
        all_events.sort(key=lambda e: e.event_score, reverse=True)
        self.events = all_events
        return all_events

    def get_top_events(self, n: int = 10, min_score: float = 60) -> List[EventRecord]:
        """获取评分最高的N个事件"""
        return [e for e in self.events if e.event_score >= min_score][:n]

    def save_events(self) -> None:
        """保存事件列表"""
        today = datetime.now().strftime("%Y%m%d")
        path = self.EVENT_DIR / f"events_{today}.json"
        data = [asdict(e) for e in self.events]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_events(self, date_str: str = None) -> List[EventRecord]:
        """加载历史事件"""
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")
        path = self.EVENT_DIR / f"events_{date_str}.json"
        if not path.exists():
            return []
        data = safe_read_json(path)
        if data is None:
            return []
        return [EventRecord(**d) for d in data]


# ═══════════════════════════════════════════════════════════════
# CLI入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="天枢事件驱动策略引擎")
    parser.add_argument("action", choices=["scan", "list", "score", "detect"],
                        default="scan", nargs="?")
    parser.add_argument("--event", help="指定事件类型(EV-01~07)")
    parser.add_argument("--code", help="指定股票代码")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    engine = CombinedEventEngine()
    engine.register_detector(EarningsSurpriseDetector(engine.ds))

    # 注册其他检测器（仅初始化，实现在后续任务中）
    engine.register_detector(type("MomentumDetector", (EventDetector,), {
        "__init__": lambda self, ds: EventDetector.__init__(self, "EV-02", "价格动量突破", ds),
        "detect": lambda self, stocks: [],
    })(engine.ds))

    if args.action == "scan":
        events = engine.scan_all()
        print(f"\n📊 共检测 {len(events)} 条事件")
        for e in engine.get_top_events(args.top):
            print(f"  [{e.event_type}] {e.name}({e.code}) 评分{e.event_score:.0f}")
            for k, v in e.signals.items():
                print(f"    ├ {k}: {v}")
        engine.save_events()
        print(f"\n✅ 已保存至 {engine.EVENT_DIR}")

    elif args.action == "list":
        events = engine.load_events()
        print(f"📋 历史事件: {len(events)} 条")
        filtered = [e for e in events if not args.event or e.event_type == args.event]
        for e in sorted(filtered, key=lambda x: x.event_score, reverse=True)[:args.top]:
            print(f"  [{e.event_type}] {e.name}({e.code}) {e.trigger_date} 评分{e.event_score:.0f}")

    elif args.action == "score":
        if not args.code:
            print("请指定 --code")
        else:
            stock = engine.ds.get_quote(args.code)
            kline = engine.ds.get_kline(args.code)
            for etype in ["EV-01", "EV-02", "EV-03", "EV-05"]:
                s, sig, t, m = EventScorer.calc_composite_score(
                    etype, engine.ds.parse_eastmoney_item(
                        {"f12": args.code, "f37": 15, "f41": 30, "f46": 50}),
                    kline)
                print(f"  {etype}: 综合{s:.0f} 强度/技术/市场")
                for k, v in list(sig.items())[:3]:
                    print(f"    ├ {k}: {v}")