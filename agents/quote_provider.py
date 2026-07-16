#!/usr/bin/env python3
"""天枢统一可复用组件 — 行情接口/安全类型转换/路径管理

RF-001: 统一行情接口（替代4套实现）
RF-002: 安全类型转换
RF-003: 统一路径管理
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple


# ═══════════════════════════════════════════════════════════════
# RF-002: 安全类型转换
# ═══════════════════════════════════════════════════════════════

def safe_float(v, default: float = 0.0) -> float:
    """安全转float，处理None/'-'/''等异常值"""
    if v is None:
        return default
    try:
        return float(v) if str(v).strip() not in ('', '-', 'None') else default
    except (ValueError, TypeError):
        return default


def safe_int(v, default: int = 0) -> int:
    """安全转int"""
    if v is None:
        return default
    try:
        return int(v) if str(v).strip() not in ('', '-', 'None') else default
    except (ValueError, TypeError):
        return default


def safe_str(v, default: str = "") -> str:
    """安全转str"""
    if v is None:
        return default
    return str(v).strip()


# ═══════════════════════════════════════════════════════════════
# RF-003: 统一路径管理
# ═══════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def get_project_root() -> Path:
    """获取项目根目录"""
    return PROJECT_ROOT


def get_pool_path(pool_name: str) -> Path:
    """获取池文件路径"""
    return PROJECT_ROOT / "五池管理" / f"{pool_name}.json"


def get_data_path(*parts: str) -> Path:
    """获取data下子路径"""
    return PROJECT_ROOT / "data" / Path(*parts)


def get_history_path(date_str: str = None, suffix: str = "md") -> Path:
    """获取历史记录路径"""
    d = date_str or datetime.now().strftime("%Y-%m-%d")
    return get_data_path("历史记录") / f"{d}_{suffix}.md"


def ensure_dir(path: Path) -> Path:
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)
    return path


# ═══════════════════════════════════════════════════════════════
# RF-001: 统一行情接口（替代4套实现）
# ═══════════════════════════════════════════════════════════════

class QuoteProvider:
    """统一行情数据提供者

    替代：
    - agents/conditional_order.py MarketDataFeed
    - agents/event_engine.py DataSource.get_quote
    - agents/market_agent.py fetch_quotes
    - agents/quote_service.py QuoteService
    """

    # 腾讯行情API
    TENCENT_URL = "https://qt.gtimg.cn/q={prefix}{code}"
    # 东方财富批量行情
    EASTMONEY_URL = ("https://82.push2.eastmoney.com/api/qt/clist/get?"
                     "pn=1&pz=500&po=1&np=1"
                     "&ut=bd1d9ddb04089700cf9c27f6f7426281"
                     "&fltt=2&invt=2&fid=f3"
                     "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
                     "&fields=f12,f14,f3,f25,f20,f9,f23,f38,f37,f41,f46,f48,f100,f115,f152")
    # Sina K线
    SINA_KLINE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                  "CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={days}")

    _cache: Dict[str, Any] = {}
    _cache_time: Dict[str, float] = {}
    CACHE_TTL = 300  # 5分钟

    @classmethod
    def _code_prefix(cls, code: str) -> str:
        if code.startswith('6'):
            return 'sh'
        elif code.startswith('8') or code.startswith('4'):
            return 'bj'
        return 'sz'

    @classmethod
    def _fetch_text(cls, url: str, encoding: str = 'utf-8',
                    timeout: int = 10) -> Optional[str]:
        """带缓存的文本请求"""
        cache_key = f"txt:{url}"
        now = time.time()
        if cache_key in cls._cache and now - cls._cache_time.get(cache_key, 0) < cls.CACHE_TTL:
            return cls._cache[cache_key]
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
            })
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            text = raw.decode(encoding, errors='replace')
            cls._cache[cache_key] = text
            cls._cache_time[cache_key] = now
            return text
        except Exception:
            return None

    @classmethod
    def _fetch_json(cls, url: str, timeout: int = 15) -> Optional[dict]:
        """带缓存的JSON请求"""
        cache_key = f"json:{url}"
        now = time.time()
        if cache_key in cls._cache and now - cls._cache_time.get(cache_key, 0) < cls.CACHE_TTL:
            return cls._cache[cache_key]
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
            })
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            data = json.loads(raw.decode('utf-8', errors='replace'))
            cls._cache[cache_key] = data
            cls._cache_time[cache_key] = time.time()
            return data
        except Exception:
            return None

    @classmethod
    def fetch_quote(cls, code: str) -> Optional[Dict[str, Any]]:
        """获取个股实时行情（腾讯API）"""
        prefix = cls._code_prefix(code)
        url = cls.TENCENT_URL.format(prefix=prefix, code=code)
        text = cls._fetch_text(url, encoding='gbk')
        if not text:
            return None
        try:
            parts = text.split('~')
            if len(parts) < 40:
                return None
            return {
                "code": code,
                "name": parts[1] if len(parts) > 1 else "",
                "price": safe_float(parts[3]),
                "chg_pct": safe_float(parts[32]),
                "high": safe_float(parts[33]),
                "low": safe_float(parts[34]),
                "volume": safe_int(parts[6]),
                "turnover": safe_float(parts[38]),
                "pe": safe_float(parts[39]),
                "amplitude": safe_float(parts[43]),
                "vol_ratio": safe_float(parts[49]),
                "month_chg": safe_float(parts[63]),
                "quarter_chg": safe_float(parts[64]),
            }
        except (ValueError, IndexError):
            return None

    @classmethod
    def fetch_batch(cls, codes: List[str]) -> Dict[str, Dict]:
        """批量获取行情"""
        result = {}
        for code in codes:
            quote = cls.fetch_quote(code)
            if quote:
                result[code] = quote
            time.sleep(0.1)
        return result

    @classmethod
    def fetch_kline(cls, code: str, days: int = 200) -> List[Dict]:
        """获取K线数据（Sina API）"""
        prefix = cls._code_prefix(code)
        url = cls.SINA_KLINE.format(prefix=prefix, code=code, days=days)
        data = cls._fetch_json(url)
        if not data:
            time.sleep(1.2)
            data = cls._fetch_json(url)
        return data if isinstance(data, list) else []

    @classmethod
    def fetch_all_stocks(cls, fields: str = None) -> List[Dict[str, Any]]:
        """获取全A股行情+财务数据（东方财富API）"""
        all_items = []
        f = fields or "f12,f14,f3,f25,f20,f9,f23,f38,f37,f41,f46,f48,f100,f115,f152"
        for page in range(1, 60):
            url = (f"https://82.push2.eastmoney.com/api/qt/clist/get?"
                   f"pn={page}&pz=500&po=1&np=1"
                   f"&ut=bd1d9ddb04089700cf9c27f6f7426281"
                   f"&fltt=2&invt=2&fid=f20"
                   f"&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
                   f"&fields={f}")
            data = cls._fetch_json(url)
            if not data:
                # curl降级
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

    @classmethod
    def parse_stock_item(cls, item: dict) -> Dict[str, Any]:
        """解析东方财富返回条目为标准格式"""
        return {
            "code": safe_str(item.get("f12")),
            "name": safe_str(item.get("f14")),
            "industry": safe_str(item.get("f100")),
            "price": safe_float(item.get("f3")),
            "chg_pct": safe_float(item.get("f25")),
            "market_cap": safe_float(item.get("f20")),
            "pe": safe_float(item.get("f9")),
            "pb": safe_float(item.get("f23")),
            "roe": safe_float(item.get("f37")),
            "rev_yoy": safe_float(item.get("f41")),
            "np_yoy": safe_float(item.get("f46")),
            "np_qoq": safe_float(item.get("f48")),
            "turnover": safe_float(item.get("f38")),
            "undistributed_profit": safe_float(item.get("f115")),
            "capital_reserve": safe_float(item.get("f152")),
        }

    @classmethod
    def calc_ma(cls, closes: List[float], period: int) -> float:
        """计算移动平均线"""
        if len(closes) < period:
            return 0
        return sum(closes[-period:]) / period