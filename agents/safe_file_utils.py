#!/usr/bin/env python3
"""
Safe File Utils — 天枢权衡安全文件读写工具
替代裸 open()/read_text()，提供统一异常保护和日志记录
"""

import os
import json
import logging
import functools
import time
from pathlib import Path
from typing import Optional, Any, Dict, List, Union
from datetime import datetime

logger = logging.getLogger(__name__)


def retry(max_attempts=3, delay=2, backoff=2):
    """重试装饰器：指数退避"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        wait = delay * (backoff ** attempt)
                        time.sleep(wait)
            raise last_exc
        return wrapper
    return decorator


class FileReadError(Exception):
    """文件读取异常"""
    pass


class FileWriteError(Exception):
    """文件写入异常"""
    pass


@retry()
def safe_read_file(
    path: Union[str, Path],
    encoding: str = "utf-8",
    default: Optional[Any] = None,
    required: bool = False,
    log_error: bool = True
) -> Optional[str]:
    """
    安全读取文本文件，带异常保护
    
    Args:
        path: 文件路径
        encoding: 编码
        default: 读取失败时返回的默认值（required=False 时）
        required: 是否必须成功，失败抛异常
        log_error: 是否记录错误日志
    
    Returns:
        文件内容字符串，或 default
    
    Raises:
        FileReadError: required=True 且读取失败
    """
    path = Path(path)
    
    if not path.exists():
        if required:
            err = FileReadError(f"必需文件不存在: {path}")
            if log_error:
                logger.error(f"[SafeFile] {err}")
            raise err
        if log_error:
            logger.debug(f"[SafeFile] 文件不存在（非必需）: {path}")
        return default
    
    try:
        content = path.read_text(encoding=encoding)
        return content
    except PermissionError:
        err = FileReadError(f"权限不足: {path}")
        if log_error:
            logger.error(f"[SafeFile] {err}")
        if required:
            raise err
        return default
    except UnicodeDecodeError:
        err = FileReadError(f"编码错误 ({encoding}): {path}")
        if log_error:
            logger.error(f"[SafeFile] {err}")
        if required:
            raise err
        return default
    except Exception as e:
        err = FileReadError(f"读取失败 {path}: {e}")
        if log_error:
            logger.error(f"[SafeFile] {err}")
        if required:
            raise err
        return default


def safe_read_json(
    path: Union[str, Path],
    default: Optional[Dict] = None,
    required: bool = False,
    log_error: bool = True
) -> Optional[Dict]:
    """
    安全读取 JSON 文件
    
    Args:
        path: 文件路径
        default: 读取失败时返回的默认值
        required: 是否必须成功
        log_error: 是否记录错误日志
    
    Returns:
        解析后的字典，或 default
    """
    content = safe_read_file(path, required=required, log_error=log_error)
    if content is None:
        return default
    
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        err = FileReadError(f"JSON 解析失败 {path}: {e}")
        if log_error:
            logger.error(f"[SafeFile] {err}")
        if required:
            raise err
        return default


@retry()
def safe_write_file(
    path: Union[str, Path],
    content: str,
    encoding: str = "utf-8",
    log_error: bool = True
) -> bool:
    """
    安全写入文本文件，自动创建父目录
    
    Args:
        path: 文件路径
        content: 内容
        encoding: 编码
        log_error: 是否记录错误日志
    
    Returns:
        是否成功
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)
        return True
    except PermissionError:
        err = FileWriteError(f"权限不足，无法写入: {path}")
        if log_error:
            logger.error(f"[SafeFile] {err}")
        return False
    except Exception as e:
        err = FileWriteError(f"写入失败 {path}: {e}")
        if log_error:
            logger.error(f"[SafeFile] {err}")
        return False


def safe_write_json(
    path: Union[str, Path],
    data: Dict,
    indent: int = 2,
    ensure_ascii: bool = False,
    log_error: bool = True
) -> bool:
    """
    安全写入 JSON 文件
    
    Args:
        path: 文件路径
        data: 数据
        indent: 缩进
        ensure_ascii: 是否转义中文
        log_error: 是否记录错误日志
    
    Returns:
        是否成功
    """
    try:
        content = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
        return safe_write_file(path, content, log_error=log_error)
    except Exception as e:
        err = FileWriteError(f"JSON 序列化失败: {e}")
        if log_error:
            logger.error(f"[SafeFile] {err}")
        return False


def safe_append_file(
    path: Union[str, Path],
    content: str,
    encoding: str = "utf-8",
    log_error: bool = True
) -> bool:
    """
    安全追加内容到文件
    
    Args:
        path: 文件路径
        content: 要追加的内容
        encoding: 编码
        log_error: 是否记录错误日志
    
    Returns:
        是否成功
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding=encoding) as f:
            f.write(content)
        return True
    except Exception as e:
        err = FileWriteError(f"追加失败 {path}: {e}")
        if log_error:
            logger.error(f"[SafeFile] {err}")
        return False


def file_exists(path: Union[str, Path]) -> bool:
    """检查文件是否存在"""
    return Path(path).exists()


def ensure_dir(path: Union[str, Path]) -> Path:
    """确保目录存在，返回 Path 对象"""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── 敏感信息扫描 ─────────────────────────────────────────────

SENSITIVE_PATTERNS = [
    # API 密钥模式
    (r'(?i)(api[_-]?key|apikey)\s*[=:]\s*["\']?[A-Za-z0-9_\-]{20,}', 'API密钥硬编码'),
    (r'(?i)(secret|token)\s*[=:]\s*["\']?[A-Za-z0-9_\-]{16,}', 'Secret/Token硬编码'),
    # 密码模式
    (r'(?i)(password|passwd|pwd)\s*[=:]\s*["\']?\S{4,}', '密码硬编码'),
    # 私有密钥
    (r'-----BEGIN (RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----', '私有密钥'),
    # 连接字符串
    (r'(?i)(mysql|postgres|mongodb|redis)://\S+:\S+@', '数据库连接串含密码'),
    # 环境变量中的敏感值（检查是否被硬编码而非从env读取）
    (r'(?i)(OPENCODE_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*=\s*["\']?[A-Za-z0-9]', 'API密钥硬编码'),
]


def scan_sensitive_files(
    paths: List[Union[str, Path]],
    exclude_dirs: List[str] = None
) -> List[Dict]:
    """
    扫描文件中的敏感信息
    
    Args:
        paths: 要扫描的文件或目录路径
        exclude_dirs: 排除的目录名
    
    Returns:
        发现的敏感信息列表
    """
    exclude_dirs = exclude_dirs or ['.venv', '__pycache__', 'node_modules', '.git']
    findings = []
    
    for base_path in paths:
        base_path = Path(base_path)
        if base_path.is_file():
            file_list = [base_path]
        else:
            file_list = []
            for root, dirs, files in os.walk(base_path):
                # 排除敏感目录
                dirs[:] = [d for d in dirs if d not in exclude_dirs]
                for f in files:
                    if f.endswith('.py') or f.endswith('.yaml') or f.endswith('.yml') or f.endswith('.json'):
                        file_list.append(Path(root) / f)
        
        for fp in file_list:
            try:
                content = fp.read_text(encoding='utf-8', errors='ignore')
                for pattern, desc in SENSITIVE_PATTERNS:
                    matches = re.finditer(pattern, content)
                    for m in matches:
                        # 跳过注释行
                        line_start = content.rfind('\n', 0, m.start()) + 1
                        line_end = content.find('\n', m.start())
                        line = content[line_start:line_end].strip()
                        if line.startswith('#') or line.startswith('//'):
                            continue
                        findings.append({
                            'file': str(fp),
                            'line': content[:m.start()].count('\n') + 1,
                            'type': desc,
                            'match': m.group()[:80] + ('...' if len(m.group()) > 80 else ''),
                        })
            except Exception:
                pass
    
    return findings


if __name__ == "__main__":
    # 自测：扫描天枢项目
    findings = scan_sensitive_files([
        "/home/seven/hermes-data/tianshu-quanheng/agents",
        "/home/seven/hermes-data/tianshu-quanheng/main.py",
    ])
    if findings:
        print(f"⚠️ 发现 {len(findings)} 处敏感信息:")
        for f in findings:
            print(f"   {f['file']}:{f['line']} — {f['type']}")
            print(f"      匹配: {f['match']}")
    else:
        print("✅ 未发现敏感信息硬编码")
