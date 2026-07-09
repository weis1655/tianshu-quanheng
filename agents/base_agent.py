#!/usr/bin/env python3
"""
Base Agent Class - 提供所有Agent的共享功能
支持从config.yaml加载配置，记录运行指标
"""

import json
import sys
import requests
import os
import subprocess
import shlex
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import time
import random
from abc import ABC, abstractmethod

# 延迟导入配置和指标，避免循环导入
_config_loader = None
_metrics = None


def _get_config():
    """延迟加载配置"""
    global _config_loader
    if _config_loader is None:
        from config_loader import get_config
        _config_loader = get_config()
    return _config_loader


def _get_metrics():
    """延迟加载指标收集器"""
    global _metrics
    if _metrics is None:
        from metrics import get_metrics
        _metrics = get_metrics()
    return _metrics


class BaseAgent(ABC):
    """所有Agent的基础类，提供共享的LLM调用、错误处理和文件操作功能"""

    # 类级别的配置（优先使用配置文件）
    _config = None

    @classmethod
    def _get_config(cls):
        """获取配置（延迟加载，单例）"""
        if cls._config is None:
            cls._config = _get_config()
        return cls._config

    @classmethod
    def get_api_url(cls) -> str:
        """获取API URL"""
        return cls._get_config().get("api.opencode_url", "https://opencode.ai/zen/v1/chat/completions")

    @classmethod
    def get_api_key(cls) -> str:
        """获取API密钥（从环境变量OPENCODE_ZEN_API_KEY）"""
        return os.environ.get("OPENCODE_ZEN_API_KEY", "")

    @classmethod
    def get_default_model(cls) -> str:
        """获取默认模型"""
        return cls._get_config().get("api.default_model", "minimax-m2.5-free")

    @classmethod
    def get_llm_temperature(cls) -> float:
        """获取LLM温度参数"""
        return cls._get_config().get("api.llm.temperature", 0.3)

    @classmethod
    def get_llm_max_tokens(cls) -> int:
        """获取LLM最大token数"""
        return cls._get_config().get("api.llm.max_tokens", 2000)

    @classmethod
    def get_llm_timeout(cls) -> int:
        """获取LLM超时时间"""
        return cls._get_config().get("api.llm.timeout", 60)

    @classmethod
    def get_llm_max_retries(cls) -> int:
        """获取LLM最大重试次数"""
        return cls._get_config().get("api.llm.max_retries", 3)

    @classmethod
    def get_llm_backend(cls) -> str:
        """获取LLM后端: opencode 或 gemini"""
        return cls._get_config().get("api.llm.backend", "opencode")

    def __init__(self, agent_name: str = ""):
        """
        初始化基础Agent

        Args:
            agent_name: Agent名称，用于日志和标识
        """
        self.agent_name = agent_name or self.__class__.__name__
        self.root = Path(__file__).parent.parent.resolve()
        # 将agents目录添加到路径中，以便导入其他agents
        sys.path.insert(0, str(self.root / "agents"))
        sys.path.insert(0, str(self.root))

        # 初始化统计信息
        self.stats = {
            "llm_calls": 0,
            "llm_errors": 0,
            "start_time": datetime.now(),
            "last_call_time": None
        }

        # 记录Agent启动
        _get_metrics().record_agent_start(self.agent_name)

    def call_llm(self, prompt: str, system: str = "", max_tokens: int = None,
                 temperature: float = None, max_retries: int = None,
                 response_format: dict = None) -> str:
        """
        调用 LLM 的统一方法，包含重试机制和错误处理

        Args:
            prompt: 用户提示词
            system: 系统提示词
            max_tokens: 最大 token 数（默认从配置读取）
            temperature: 采样温度（默认从配置读取）
            max_retries: 最大重试次数（默认从配置读取）

        Returns:
            LLM 响应内容
        """
        # 根据配置选择后端
        backend = self.get_llm_backend()
        if backend == "gemini":
            return self._call_llm_gemini(prompt, system, max_tokens, temperature, max_retries, response_format)
        elif backend == "doubao":
            return self._call_llm_doubao(prompt, system, max_tokens, temperature, max_retries, response_format)
        elif backend == "sensenova":
            return self._call_llm_sensenova(prompt, system, max_tokens, temperature, max_retries, response_format)
        else:
            return self._call_llm_opencode(prompt, system, max_tokens, temperature, max_retries, response_format)

    def _log_prompt(self, agent_name: str, prompt_type: str, system: str, user: str, response: str = ""):
        """记录 LLM 提示词到文件，便于盟主排查。"""
        try:
            log_dir = Path(self.root) / "logs" / "prompts"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = log_dir / f"{agent_name}_{prompt_type}_{ts}.md"
            content = f"# LLM 提示词日志\n"
            content += f"- Agent: {agent_name}\n- 类型: {prompt_type}\n- 时间: {datetime.now().isoformat()}\n\n## System Prompt\n{system or '(无)'}\n\n## User Prompt\n{user}\n"
            if response:
                content += f"\n## LLM 回复（前500字）\n{response[:500]}\n"
            fname.write_text(content, encoding="utf-8")
        except Exception:  # 安全降级: 日志写入失败→静默降级，不影响主流程（已有注释）
            pass  # 日志失败不影响主流程

    def _call_llm_opencode(self, prompt: str, system: str, max_tokens: int,
                            temperature: float, max_retries: int,
                            response_format: dict = None) -> str:
        """调用 OpenCode API"""
        # 使用配置默认值
        if max_tokens is None:
            max_tokens = self.get_llm_max_tokens()
        if temperature is None:
            temperature = self.get_llm_temperature()
        if max_retries is None:
            max_retries = self.get_llm_max_retries()

        self.stats["llm_calls"] += 1
        self.stats["last_call_time"] = datetime.now()

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.get_default_model(),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format
        headers = {
            "Authorization": f"Bearer {self.get_api_key()}",
            "Content-Type": "application/json"
        }

        api_url = self.get_api_url()
        timeout = self.get_llm_timeout()

        # 重试机制
        for attempt in range(max_retries + 1):
            try:
                start_time = time.time()
                r = requests.post(
                    api_url,
                    headers=headers,
                    json=payload,
                    timeout=timeout
                )
                r.raise_for_status()
                data = r.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                duration = time.time() - start_time

                if content is None:
                    content = ""

                # 记录成功调用
                result = str(content) if content else "[模型返回空内容]"
                tokens_used = data.get("usage", {}).get("total_tokens", 0)
                _get_metrics().record_llm_call(
                    self.agent_name,
                    success=True,
                    tokens=tokens_used,
                    duration=duration
                )
                self._log_prompt(self.agent_name, "opencode", system, prompt, result)
                return result

            except Exception as e:
                duration = time.time() - start_time if 'start_time' in locals() else 0
                self.stats["llm_errors"] += 1

                # 记录失败调用
                _get_metrics().record_llm_call(
                    self.agent_name,
                    success=False,
                    duration=duration
                )

                if attempt < max_retries:
                    # 指数退避 + 随机抖动
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(wait_time)
                    continue
                else:
                    return f"[LLM调用失败，经 {max_retries} 次重试] {e}"

    def _call_llm_gemini(self, prompt: str, system: str, max_tokens: int,
                          temperature: float, max_retries: int,
                          response_format: dict = None) -> str:
        """调用 OpenCLI Gemini"""
        if max_retries is None:
            max_retries = self.get_llm_max_retries()
        if max_tokens is None:
            max_tokens = self.get_llm_max_tokens()

        self.stats["llm_calls"] += 1
        self.stats["last_call_time"] = datetime.now()

        # 组合 system + prompt（不截断，opencli gemini ask 支持足够长的参数）
        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        # 调用 OpenCLI Gemini
        for attempt in range(max_retries + 1):
            try:
                start_time = time.time()
                # 用 stdin 传 prompt，避免 shell 命令行长度的限制
                # opencli gemini ask 不直接支持 stdin，故退而用 shlex.quote + shell（ARG_MAX≈2MB 足够）
                safe_prompt = shlex.quote(full_prompt)
                cmd = f"opencli gemini ask {safe_prompt}"
                
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=90
                )
                duration = time.time() - start_time

                if result.returncode == 0:
                    content = result.stdout.strip()
                    if content:
                        _get_metrics().record_llm_call(
                            self.agent_name,
                            success=True,
                            duration=duration
                        )
                        self._log_prompt(self.agent_name, "gemini", system, prompt, content)
                        return content
                    else:
                        _get_metrics().record_llm_call(
                            self.agent_name,
                            success=False,
                            duration=duration
                        )
                        return "[模型返回空内容]"
                else:
                    raise Exception(f"OpenCLI exit code: {result.returncode}")

            except Exception as e:
                duration = time.time() - start_time if 'start_time' in locals() else 0
                self.stats["llm_errors"] += 1

                _get_metrics().record_llm_call(
                    self.agent_name,
                    success=False,
                    duration=duration
                )

                if attempt < max_retries:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(wait_time)
                    continue
                else:
                    return f"[LLM调用失败，经 {max_retries} 次重试] {e}"

    def _call_llm_doubao(self, prompt: str, system: str, max_tokens: int,
                          temperature: float, max_retries: int,
                          response_format: dict = None) -> str:
        """调用 OpenCLI Doubao（字节豆包）"""
        if max_retries is None:
            max_retries = self.get_llm_max_retries()
        if max_tokens is None:
            max_tokens = self.get_llm_max_tokens()

        self.stats["llm_calls"] += 1
        self.stats["last_call_time"] = datetime.now()

        # 组合 system + prompt
        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        # 调用 OpenCLI Doubao
        for attempt in range(max_retries + 1):
            try:
                start_time = time.time()
                safe_prompt = shlex.quote(full_prompt)
                cmd = f"opencli doubao ask {safe_prompt}"

                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=90
                )
                duration = time.time() - start_time

                if result.returncode == 0:
                    content = result.stdout.strip()
                    # 解析 opencli doubao ask 的输出格式：
                    # - Role: User
                    #   Text: <user prompt>
                    # - Role: Assistant
                    #   Text: <assistant response>
                    # 只提取 Assistant 的回复内容
                    assistant_text = ""
                    in_assistant = False
                    for line in content.split("\n"):
                        stripped = line.strip()
                        if "Role: Assistant" in stripped or "role: assistant" in stripped.lower():
                            in_assistant = True
                            continue
                        if in_assistant and stripped.startswith("Text:"):
                            assistant_text = stripped[5:].strip()  # 去掉 "Text:" 前缀
                            break
                        # 如果遇到新的 Role 行，停止
                        if stripped.startswith("- Role:") or stripped.startswith("Role:"):
                            in_assistant = False

                    if assistant_text:
                        content = assistant_text
                    else:
                        # 兜底：过滤噪音后返回剩余内容
                        lines = []
                        skip_kw = [
                            "update available", "extension update", "npm install",
                            "download:", "node:", "undici",
                            "- role:", "role: user", "role: assistant",
                            "text: |-", "text:",
                            # Doubao UI 噪音
                            "快速 ppt", "图像生成", "帮我写作", "翻译", "编程", "更多",
                            # 提示词回声（LLM 不应输出这些）
                            "如果新闻内容不足", "请输出：", "[无新闻数据]",
                            # Doubao AI 免责声明
                            "本回答由 ai 生成", "仅供参考", "请仔细甄别", "请咨询专业人士",
                        ]
                        for line in content.split("\n"):
                            stripped = line.strip()
                            if not stripped:
                                continue
                            # 跳过包含噪音关键词的行
                            if any(kw in stripped.lower() for kw in skip_kw):
                                continue
                            # 跳过 SOUL/宪法 标题（这是 system prompt 的一部分，不应出现在输出中）
                            if stripped.startswith("【天枢宪法") or stripped.startswith("【角色定义") or stripped.startswith("【全局约束"):
                                continue
                            lines.append(stripped)
                        content = "\n".join(lines).strip()

                    if content:
                        _get_metrics().record_llm_call(
                            self.agent_name,
                            success=True,
                            duration=duration
                        )
                        self._log_prompt(self.agent_name, "doubao", system, prompt, content)
                        return content
                    else:
                        _get_metrics().record_llm_call(
                            self.agent_name,
                            success=False,
                            duration=duration
                        )
                        return "[模型返回空内容]"
                else:
                    raise Exception(f"OpenCLI exit code: {result.returncode}")

            except Exception as e:
                duration = time.time() - start_time if 'start_time' in locals() else 0
                self.stats["llm_errors"] += 1

                _get_metrics().record_llm_call(
                    self.agent_name,
                    success=False,
                    duration=duration
                )

                if attempt < max_retries:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(wait_time)
                    continue
                else:
                    return f"[LLM调用失败，经 {max_retries} 次重试] {e}"

    def _call_llm_sensenova(self, prompt: str, system: str, max_tokens: int,
                             temperature: float, max_retries: int,
                             response_format: dict = None) -> str:
        """调用 SenseNova API（商汤大模型，OpenAI 兼容格式）"""
        import os
        import requests

        if max_retries is None:
            max_retries = self.get_llm_max_retries()
        if max_tokens is None:
            max_tokens = self.get_llm_max_tokens()
        if temperature is None:
            temperature = self.get_llm_temperature()

        self.stats["llm_calls"] += 1
        self.stats["last_call_time"] = datetime.now()

        # 从环境变量获取 SenseNova API 配置
        api_key = os.environ.get("SENSENOVA_API_KEY") or os.environ.get("SN_API_KEY") or os.environ.get("SN_CHAT_API_KEY")
        base_url = os.environ.get("SN_CHAT_BASE_URL") or os.environ.get("SN_BASE_URL") or "https://token.sensenova.cn/v1"
        model = os.environ.get("SN_CHAT_MODEL") or "sensenova-6.7-flash-lite"
        # 降级模型：主模型超时/限流失败后尝试
        fallback_model = "sensenova-6.7-flash-lite"

        if not api_key:
            return "[SenseNova: API Key 未配置]"

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        timeout = self.get_llm_timeout()

        for attempt in range(max_retries + 1):
            # 第1次重试用降级模型（主模型的所有重试都失败后）
            use_fallback = attempt > 0 and attempt == max_retries and model != fallback_model
            current_model = fallback_model if use_fallback else model
            if use_fallback:
                print(f"[LLM降级] ⚠️ {model} 重试{max_retries}次均失败，降级至 {fallback_model}")
            payload["model"] = current_model
            try:
                start_time = time.time()
                r = requests.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=timeout
                )
                r.raise_for_status()
                data = r.json()
                # 商汤 API 可能将内容放在 reasoning 或 content 字段
                message = data.get("choices", [{}])[0].get("message", {})
                content = message.get("content") or message.get("reasoning") or ""
                duration = time.time() - start_time

                if content is None:
                    content = ""

                if content:
                    tokens_used = data.get("usage", {}).get("total_tokens", 0)
                    _get_metrics().record_llm_call(
                        self.agent_name,
                        success=True,
                        tokens=tokens_used,
                        duration=duration
                    )
                    self._log_prompt(self.agent_name, "sensenova", system, prompt, content)
                    return content
                else:
                    _get_metrics().record_llm_call(
                        self.agent_name,
                        success=False,
                        duration=duration
                    )
                    return "[模型返回空内容]"

            except Exception as e:
                duration = time.time() - start_time if 'start_time' in locals() else 0
                self.stats["llm_errors"] += 1

                _get_metrics().record_llm_call(
                    self.agent_name,
                    success=False,
                    duration=duration
                )

                if attempt < max_retries:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(wait_time)
                    continue
                else:
                    return f"[LLM调用失败，经 {max_retries} 次重试] {e}"

    def safe_read_json(self, file_path: Path, default: Any = None) -> Any:
        """
        安全读取JSON文件
        
        Args:
            file_path: 文件路径
            default: 读取失败时的默认值
            
        Returns:
            文件内容或默认值
        """
        try:
            if file_path.exists():
                return json.loads(file_path.read_text(encoding="utf-8"))
            else:
                return default if default is not None else {}
        except Exception as e:
            print(f"[{self.agent_name}] 读取JSON文件失败 {file_path}: {e}")
            return default if default is not None else {}
    
    def safe_write_json(self, file_path: Path, data: Any, indent: int = 2) -> bool:
        """
        安全写入JSON文件
        
        Args:
            file_path: 文件路径
            data: 要写入的数据
            indent: 缩进空格数
            
        Returns:
            是否成功
        """
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=indent),
                encoding="utf-8"
            )
            return True
        except Exception as e:
            print(f"[{self.agent_name}] 写入JSON文件失败 {file_path}: {e}")
            return False
    
    def safe_read_text(self, file_path: Path, default: str = "") -> str:
        """
        安全读取文本文件
        
        Args:
            file_path: 文件路径
            default: 读取失败时的默认值
            
        Returns:
            文件内容或默认值
        """
        from safe_file_utils import safe_read_file
        return safe_read_file(file_path, default=default, required=False, log_error=False)
    
    def safe_write_text(self, file_path: Path, content: str) -> bool:
        """
        安全写入文本文件
        
        Args:
            file_path: 文件路径
            content: 要写入的内容
            
        Returns:
            是否成功
        """
        from safe_file_utils import safe_write_file
        return safe_write_file(file_path, content)
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取Agent统计信息
        
        Returns:
            统计信息字典
        """
        stats = self.stats.copy()
        if stats["start_time"]:
            stats["runtime_seconds"] = (datetime.now() - stats["start_time"]).total_seconds()
        return stats
    
    def reset_stats(self):
        """重置统计信息"""
        self.stats = {
            "llm_calls": 0,
            "llm_errors": 0,
            "start_time": datetime.now(),
            "last_call_time": None
        }
    
    @abstractmethod
    def run(self, *args, **kwargs) -> Dict[str, Any]:
        pass


def load_tianshu_soul(agent_name: str = "") -> str:
    """加载SOUL：优先读 agents/soul/{AgentName}_SOUL.md，降级读 skill/references/SOUL.md"""
    # 优先级1：agents/soul/{AgentName}_SOUL.md（各Agent专属SOUL）
    if agent_name:
        agent_dir = Path(__file__).parent
        soul_file = agent_dir / "soul" / f"{agent_name}_SOUL.md"
        if soul_file.exists():
            content = soul_file.read_text(encoding="utf-8")
            # 跳过 YAML frontmatter
            lines = content.split("\n")
            start = 0
            if lines and lines[0].strip() == "---":
                for i, l in enumerate(lines[1:], 1):
                    if l.strip() == "---":
                        start = i + 1
                        break
            return "\n".join(lines[start:]).strip()
    
    # 优先级2：skill/references/SOUL.md（天枢专属扩展层）
    agent_dir = Path(__file__).parent
    skill_soul = agent_dir / "skill" / "references" / "SOUL.md"
    if skill_soul.exists():
        return skill_soul.read_text(encoding="utf-8").strip()
    
    # 降级读根目录 SOUL.md
    main_soul = agent_dir.parent / "SOUL.md"
    if main_soul.exists():
        return main_soul.read_text(encoding="utf-8").strip()
    return ""


def build_agent_system_prompt(role_prompt: str, agent_name: str = "", extra_context: str = "") -> str:
    """构建 Agent system prompt：SOUL（优先Agent专属） + 角色定义 + 额外记忆上下文"""
    soul = load_tianshu_soul(agent_name) if agent_name else ""
    header = f"【天枢宪法 | {agent_name}】\n\n" if agent_name else ""
    
    parts = []
    if soul:
        parts.append(header + soul)
    if role_prompt:
        parts.append(f"【角色定义】\n{role_prompt}")
    if extra_context:
        parts.append(f"【跨天记忆上下文】\n{extra_context.strip()}")
    parts.append(
        "【全局约束】\n"
        "- 只输出事实和分析，不编造数据\n"
        "- 涉及投资决策时，说明置信度\n"
        "- 完成写入文件后，输出「✅ 完成」，不再发送额外消息\n"
        "- 禁止发送无意义的确认消息或表情"
    )
    return "\n\n".join(parts)


def add_market_prefix(code: str) -> str:
    """
    为股票代码添加市场前缀（sh/sz），用于腾讯API
    
    Args:
        code: 原始股票代码（如601899）
        
    Returns:
        带市场前缀的代码（如sh601899）
    """
    if not code:
        return ""
    code = code.strip().upper()
    # 移除任何现有的前缀/后缀
    code = code.replace(".SH", "").replace(".SZ", "").replace("SH", "").replace("SZ", "")
    if len(code) == 6 and code.isdigit():
        market = "sh" if code.startswith(("6", "5")) else "sz"
        return f"{market}{code}"
    return ""  # 如果不是有效的6位数字代码，返回空字符串


def validate_and_prefix_codes(codes: List[str]) -> List[str]:
    """
    验证股票代码列表并添加市场前缀
    
    Args:
        codes: 原始股票代码列表
        
    Returns:
        市场前缀已添加的有效代码列表
    """
    prefixed_codes = []
    for code in codes:
        if code:
            prefixed = add_market_prefix(code)
            if prefixed:  # 只添加非空结果
                prefixed_codes.append(prefixed)
    return prefixed_codes