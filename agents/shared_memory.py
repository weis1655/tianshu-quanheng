#!/usr/bin/env python3
"""
共享内存池 - 天枢权衡多Agent通信桥梁（简化版）
"""

import json
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from safe_file_utils import safe_read_json, safe_write_file

logger = logging.getLogger(__name__)


class SharedMemory:
    """共享内存池（文件版，线程安全）"""
    
    def __init__(self, root: Path = None):
        self.root = root or Path(__file__).parent.parent.resolve()
        self.file = self.root / "data" / "shared_memory.json"
        self.file.parent.mkdir(parents=True, exist_ok=True)
    
    def _read(self) -> dict:
        if self.file.exists():
            data = safe_read_json(self.file, default=None, required=False, log_error=False)
            if data is not None:
                return data
        return self._empty()
    
    def _empty(self) -> dict:
        return {
            "version": "1.0",
            "created": datetime.now().isoformat(),
            "updated": datetime.now().isoformat(),
            "stage": "idle",
            "market": {"status": "pending", "timestamp": None, "stocks": []},
            "news": {"status": "pending", "timestamp": None, "summary": "", "drivers": []},
            "fundamental": {"status": "pending", "timestamp": None, "scores": {}},
            "decision": {"status": "pending", "timestamp": None, "recommendations": []},
            "review": {"status": "pending", "timestamp": None, "logs": []},
        }
    
    def read(self) -> dict:
        return self._read()
    
    def update(self, key: str, value: Any):
        data = self._read()
        data[key] = value
        data["updated"] = datetime.now().isoformat()
        success = safe_write_file(self.file, json.dumps(data, ensure_ascii=False, indent=2))
        if not success:
            logger.error(f"[SharedMemory] 更新失败: {self.file}")
    
    def get(self, key: str, default: Any = None) -> Any:
        return self._read().get(key, default)
    
    def reset(self, stage: str = "full_cycle"):
        data = self._empty()
        data["stage"] = stage
        success = safe_write_file(self.file, json.dumps(data, ensure_ascii=False, indent=2))
        if not success:
            logger.error(f"[SharedMemory] 重置失败: {self.file}")


def get_memory(root: Path = None) -> SharedMemory:
    return SharedMemory(root)