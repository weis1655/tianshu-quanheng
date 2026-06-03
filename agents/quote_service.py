"""QuoteService - 共享行情缓存（session级）"""
import time
from datetime import datetime
from typing import Optional, Dict, List, Any

class QuoteService:
    """Session-level quote cache. 
    Same session_id within a pipeline run returns same data for consistency.
    """
    _session_data: Dict[str, Any] = {}  # {session_id: {purpose: {code: quote}}}

    @classmethod
    def init_session(cls, session_id: str):
        """在 main.py 流程开始时调用，初始化新 session"""
        cls._session_data[session_id] = {}
        # 保留最近3个session的缓存用于回溯
        old_sessions = sorted(cls._session_data.keys())[:-3]
        for s in old_sessions:
            del cls._session_data[s]

    @classmethod
    def get_prices(cls, codes: List[str], session_id: str = None, 
                   purpose: str = 'realtime') -> Dict[str, dict]:
        """获取行情，按 session + purpose 缓存"""
        if not codes:
            return {}
        if not session_id or session_id not in cls._session_data:
            # 无 session 时直接拉取
            from market_agent import fetch_quotes, to_api
            api_codes = [to_api(c) for c in codes if c]
            result = {}
            for q in fetch_quotes(api_codes):
                result[q.get("代码", "")] = q
            return result

        session = cls._session_data[session_id]
        if purpose not in session:
            session[purpose] = {}
        
        # 检查哪些code还没缓存
        cached = session[purpose]
        missing = [c for c in codes if c not in cached]
        
        if missing:
            from market_agent import fetch_quotes, to_api
            api_codes = [to_api(c) for c in missing if c]
            for q in fetch_quotes(api_codes):
                code = q.get("代码", "")
                if code:
                    cached[code] = q
        
        return {c: cached.get(c) for c in codes if c in cached}

    @classmethod
    def clear_session(cls, session_id: str):
        cls._session_data.pop(session_id, None)
