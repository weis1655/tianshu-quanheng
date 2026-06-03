#!/usr/bin/env python3
"""
记忆层模块 - 缓存与复用

功能：
1. 相同驱动直接缓存，避免重复LLM调用
2. 板块分析缓存
3. 驱动特征缓存

缓存策略：
- 相同驱动(AI算力/半导体等) → 直接返回缓存
- 缓存有效期: 24小时
"""

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def normalize_drive(drive_text: str) -> str:
    """标准化驱动文本，提取关键特征"""
    if not drive_text:
        return ""
    
    # 提取关键行业/概念
    sectors = re.findall(
        r"(AI算力|光模块|半导体|新能源|汽车|医药|军工|芯片|云计算|数字经济|卫星|机器人|储能|电力|石油|煤炭|算力|CPO)",
        drive_text
    )
    
    if sectors:
        return ":".join(sorted(set(sectors)))
    else:
        # 取前50字符作为特征
        return drive_text[:50]


def drive_cache_key(drive_text: str) -> str:
    """生成驱动缓存key"""
    normalized = normalize_drive(drive_text)
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


class MemoryCache:
    """记忆缓存"""
    
    def __init__(self):
        self.cache_dir = PROJECT_ROOT / "data" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_hours = 24
    
    def get(self, key: str) -> Optional[dict]:
        """获取缓存"""
        cache_file = self.cache_dir / f"{key}.json"
        if not cache_file.exists():
            return None
        
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            cached_at = datetime.strptime(data.get("cached_at", "2000-01-01"), "%Y-%m-%d %H:%M:%S")
            
            # 检查过期
            if datetime.now() - cached_at > timedelta(hours=self.ttl_hours):
                return None
            
            return data.get("content")
        except Exception:
            return None
    
    def set(self, key: str, content: dict) -> None:
        """设置缓存"""
        cache_file = self.cache_dir / f"{key}.json"
        data = {
            "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content": content,
        }
        cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    def clear_expired(self) -> None:
        """清理过期缓存"""
        count = 0
        for f in self.cache_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                cached_at = datetime.strptime(data.get("cached_at", "2000-01-01"), "%Y-%m-%d %H:%M:%S")
                
                if datetime.now() - cached_at > timedelta(hours=self.ttl_hours):
                    f.unlink()
                    count += 1
            except Exception:
                continue
        
        if count:
            print(f"清理 {count} 个过期缓存")


def get_cached_analysis(drive_text: str) -> Optional[dict]:
    """获取缓存的分析结果"""
    cache = MemoryCache()
    key = drive_cache_key(drive_text)
    return cache.get(key)


def save_cached_analysis(drive_text: str, result: dict) -> None:
    """保存分析结果缓存"""
    cache = MemoryCache()
    key = drive_cache_key(drive_text)
    cache.set(key, result)


def get_sector_cache(sector: str) -> Optional[dict]:
    """获取板块分析缓存"""
    cache = MemoryCache()
    key = hashlib.md5(sector.encode()).hexdigest()[:12]
    return cache.get(key)


def save_sector_cache(sector: str, result: dict) -> None:
    """保存板块分析缓存"""
    cache = MemoryCache()
    key = hashlib.md5(sector.encode()).hexdigest()[:12]
    cache.set(key, result)


if __name__ == "__main__":
    # 测试
    cache = MemoryCache()
    
    # 测试缓存
    cache.set("test_key", {"result": "test_data"})
    result = cache.get("test_key")
    print(f"测试缓存: {result}")
    
    # 测试驱动特征提取
    drive1 = "AI算力产业链业绩验证，中际旭创一季报超预期"
    drive2 = "光模块持续高景气，通信板块增配居前"
    
    print(f"\n驱动特征:")
    print(f"  1: {normalize_drive(drive1)}")
    print(f"  2: {normalize_drive(drive2)}")
    print(f"  cache_key 1: {drive_cache_key(drive1)}")
    print(f"  cache_key 2: {drive_cache_key(drive2)}")