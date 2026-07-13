"""LLM 后端抽象层。

提供四种可插拔后端，统一接口：
- openai     标准 OpenAI 兼容 chat/completions（requests 实现，env 配置）
- claude-cli 本地 `claude -p <prompt> --model <model>` 子进程（无需 key，本机开发/演示）
- codex-cli  本地 `codex exec` 子进程（复用 Codex 登录；只读、临时会话、xhigh 可选）
- mock       从 JSON 文件回放（CI / 无网兜底）

对外主要 API：
- chat(messages, model=None) -> str            纯文本回复
- chat_json(messages, schema_hint) -> dict      要求模型产出 JSON，带重试+剥围栏
- target_chat(messages, model=None) -> str      被测对话模型的独立通道（TARGET_* 环境变量）

后端通过环境变量 EVALCALL_BACKEND 选择，默认 claude-cli。
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Optional

try:  # requests 仅 openai 后端需要，缺失时延迟到调用处再报错
    import requests  # type: ignore
except Exception:  # noqa: BLE001  —— 允许在无 requests 环境用 claude-cli / mock
    requests = None  # type: ignore


# ----------------------------------------------------------------------------- #
# 异常
# ----------------------------------------------------------------------------- #
class LLMError(RuntimeError):
    """LLM 调用相关的统一异常类型。"""


# ----------------------------------------------------------------------------- #
# 后端实现
# ----------------------------------------------------------------------------- #
class _BaseBackend:
    """后端基类，子类实现 chat()。"""

    name: str = "base"

    def __init__(self, model: Optional[str] = None) -> None:
        # 默认模型；调用 chat 时传入的 model 优先于此
        self.default_model: Optional[str] = model
        self._last_usage: Optional[dict[str, int]] = None
        self._last_model: Optional[str] = None

    def chat(self, messages: list[dict], model: Optional[str] = None) -> str:
        raise NotImplementedError


class OpenAIBackend(_BaseBackend):
    """标准 OpenAI 兼容 chat completions 后端（requests 实现）。

    环境变量：
    - OPENAI_BASE_URL  默认 https://api.openai.com/v1
    - OPENAI_API_KEY   必填
    - EVALCALL_MODEL   默认模型名（也可由 model 参数覆盖）
    """

    name = "openai"

    def __init__(
        self,
        model: Optional[str] = None,
        base_url_env: str = "OPENAI_BASE_URL",
        key_env: str = "OPENAI_API_KEY",
        model_env: str = "EVALCALL_MODEL",
        timeout: int = 120,
    ) -> None:
        super().__init__(model or os.getenv(model_env) or "gpt-4o-mini")
        self.base_url: str = (os.getenv(base_url_env) or "https://api.openai.com/v1").rstrip("/")
        self.api_key: Optional[str] = os.getenv(key_env)
        self.timeout: int = timeout

    def chat(self, messages: list[dict], model: Optional[str] = None) -> str:
        if requests is None:
            raise LLMError("openai 后端需要 requests，请先 pip3 install --break-system-packages requests")
        if not self.api_key:
            raise LLMError("openai 后端缺少 API key，请设置 OPENAI_API_KEY（或对应 TARGET_ 变量）")
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": 0.0,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001  —— 网络/超时统一收敛
            raise LLMError(f"openai 请求失败：{exc}") from exc
        if resp.status_code != 200:
            # 不打印完整响应体（可能含敏感信息），只取前 500 字便于排错
            raise LLMError(f"openai 返回 {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
            usage = data.get("usage") or {}
            self._last_usage = {
                "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
            }
            self._last_model = str(data.get("model") or payload["model"] or "")
            return data["choices"][0]["message"]["content"] or ""
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"openai 响应解析失败：{exc}") from exc


class ClaudeCliBackend(_BaseBackend):
    """本地 claude CLI 子进程后端：`claude -p <prompt> --model <model>`。

    把 system + 多轮 messages 拼成单个 prompt 传给 -p，stdout 即回复。
    环境变量 EVALCALL_MODEL 指定模型（默认 sonnet）。
    """

    name = "claude-cli"

    def __init__(
        self,
        model: Optional[str] = None,
        model_env: str = "EVALCALL_MODEL",
        bin_path: str = "claude",
        timeout: int = 180,
    ) -> None:
        super().__init__(model or os.getenv(model_env) or "sonnet")
        self.bin_path: str = os.getenv("CLAUDE_BIN", bin_path)
        self.timeout: int = timeout

    @staticmethod
    def _split(messages: list[dict]) -> tuple[str, str]:
        """把 messages 拆成 (system_prompt, 对话prompt)。

        system 走 claude CLI 的 --system-prompt（确保模型真正进入角色，
        而不是把指令当用户问题来分析）；user/assistant 标注说话人拼成多轮上下文。
        """
        sys_parts: list[str] = []
        parts: list[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            if role == "system":
                sys_parts.append(content)
            elif role == "assistant":
                parts.append(f"[你说]\n{content}")
            else:  # user 及其他
                parts.append(f"[对方说]\n{content}")
        if not parts:
            # 对话尚未开始（如外呼场景由模型先开口）：明确要求直接以角色身份开口
            parts.append("（对话开始，请直接说出你的第一句话）")
        parts.append("请直接输出你的下一句话本身——不要分析、不要解释、不要加任何前缀或标注。")
        sys_parts.append("你正在进行角色扮演式实时对话，始终保持角色身份，只输出台词本身。")
        return "\n\n".join(sys_parts), "\n\n".join(parts)

    def chat(self, messages: list[dict], model: Optional[str] = None) -> str:
        system_prompt, prompt = self._split(messages)
        self._last_model = str(model or self.default_model or "sonnet")
        cmd = [
            self.bin_path, "-p", prompt,
            "--model", model or self.default_model or "sonnet",
            # 上下文隔离：不加载用户/项目级 CLAUDE.md、memory、settings——
            # 否则被测/模拟模型会"知道"宿主机用户的背景信息，污染对话轨迹
            # （实测曾把用户 memory 内容泄进模拟对话，并触发角色扮演拒绝）
            "--setting-sources", "",
        ]
        if system_prompt:
            cmd += ["--system-prompt", system_prompt]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd="/tmp",  # 中立 cwd，避免吸入项目目录的任何上下文
            )
        except FileNotFoundError as exc:
            raise LLMError(f"找不到 claude 可执行文件（{self.bin_path}），请确认已安装或设置 CLAUDE_BIN") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude-cli 调用超时（>{self.timeout}s）") from exc
        if proc.returncode != 0:
            raise LLMError(f"claude-cli 退出码 {proc.returncode}: {proc.stderr.strip()[:500]}")
        return (proc.stdout or "").strip()


class CodexCliBackend(_BaseBackend):
    """本地 Codex CLI 的只读推理后端。

    该后端用于复用桌面端/CLI 已有登录来做评测实验，不让子进程操作项目：
    `--ephemeral --sandbox read-only --ignore-user-config --ignore-rules`。
    Codex CLI 只公开总 token，因此总量记录为 provider 实测，输入/输出拆分仍为估算。

    环境变量：
    - EVALCALL_MODEL / TARGET_MODEL：模型，默认 gpt-5.6-sol
    - EVALCALL_REASONING_EFFORT / TARGET_REASONING_EFFORT：默认 xhigh
    - CODEX_BIN：可执行文件，默认 codex
    """

    name = "codex-cli"
    _TOKEN_RE = re.compile(r"tokens used\s*\n\s*([0-9][0-9,]*)", re.IGNORECASE)
    _DISABLED_FEATURES = (
        "apps", "browser_use", "browser_use_external", "browser_use_full_cdp_access",
        "computer_use", "goals", "image_generation", "in_app_browser", "multi_agent",
        "plugins", "tool_suggest", "workspace_dependencies",
    )

    def __init__(
        self,
        model: Optional[str] = None,
        model_env: str = "EVALCALL_MODEL",
        effort_env: str = "EVALCALL_REASONING_EFFORT",
        timeout: Optional[int] = None,
    ) -> None:
        super().__init__(model or os.getenv(model_env) or "gpt-5.6-sol")
        self.reasoning_effort = os.getenv(effort_env) or "xhigh"
        self.bin_path = os.getenv("CODEX_BIN", "codex")
        self.timeout = timeout or int(os.getenv("EVALCALL_CODEX_TIMEOUT") or "600")
        self._last_provider_total_tokens: Optional[int] = None

    @staticmethod
    def _format_prompt(messages: list[dict]) -> str:
        parts = [
            "你是 EvalCall 的纯文本推理后端。不要调用工具，不要读取文件，不要解释任务；"
            "严格服从下面消息中的输出格式，并且只返回最终答案。"
        ]
        for message in messages:
            role = str(message.get("role") or "user").upper()
            content = str(message.get("content") or "").strip()
            if content:
                parts.append(f"[{role}]\n{content}")
        return "\n\n".join(parts)

    def chat(self, messages: list[dict], model: Optional[str] = None) -> str:
        chosen_model = str(model or self.default_model or "gpt-5.6-sol")
        self._last_model = chosen_model
        self._last_provider_total_tokens = None
        cmd = [
            self.bin_path,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--model",
            chosen_model,
            "--config",
            f'model_reasoning_effort="{self.reasoning_effort}"',
        ]
        for feature in self._DISABLED_FEATURES:
            cmd += ["--disable", feature]
        cmd.append("-")
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd="/tmp",
                start_new_session=True,
            )
            stdout, stderr = proc.communicate(
                input=self._format_prompt(messages),
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise LLMError(f"找不到 codex 可执行文件（{self.bin_path}），请安装最新版 Codex CLI") from exc
        except subprocess.TimeoutExpired as exc:
            # codex 的 Node 包装进程还会派生原生子进程。只 kill 外层会让原生
            # 子进程继续占用 stdout/stderr 管道，subprocess.run 随后永久卡在
            # communicate。用独立进程组并整体终止，保证健康检查和实时评测
            # 在超时时能准确失败并释放资源。
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.communicate()
            raise LLMError(f"codex-cli 调用超时（>{self.timeout}s）") from exc
        stderr = stderr or ""
        match = self._TOKEN_RE.search(stderr)
        if match:
            self._last_provider_total_tokens = int(match.group(1).replace(",", ""))
        if proc.returncode != 0:
            safe_tail = stderr.strip()[-800:]
            raise LLMError(f"codex-cli 退出码 {proc.returncode}: {safe_tail}")
        result = (stdout or "").strip()
        if not result:
            raise LLMError("codex-cli 未返回最终答案")
        return result


class MockBackend(_BaseBackend):
    """回放后端：从 JSON 文件按 key 取预录回复，找不到则返回固定话术。

    环境变量 EVALCALL_MOCK_FILE 指定回放文件路径。
    回放文件格式：{"<匹配键>": "<回复>", ...}，匹配键为最后一条 user 消息内容的子串。
    """

    name = "mock"
    FALLBACK = "好的，我明白了。"  # 找不到任何匹配时的固定兜底话术

    def __init__(self, model: Optional[str] = None, mock_file_env: str = "EVALCALL_MOCK_FILE") -> None:
        super().__init__(model or "mock-model")
        self._table: dict[str, str] = {}
        path = os.getenv(mock_file_env)
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._table = {str(k): str(v) for k, v in loaded.items()}
            except Exception:  # noqa: BLE001  —— mock 文件坏了不应让整个流程崩
                self._table = {}

    def chat(self, messages: list[dict], model: Optional[str] = None) -> str:
        # 取最后一条非空消息作为匹配依据
        self._last_model = str(model or self.default_model or "mock-model")
        last = ""
        for m in reversed(messages):
            if (m.get("content") or "").strip():
                last = m["content"]
                break
        for key, reply in self._table.items():
            if key and key in last:
                return reply
        return self.FALLBACK


# ----------------------------------------------------------------------------- #
# 后端工厂 + 单例缓存
# ----------------------------------------------------------------------------- #
_BACKENDS = {
    "openai": OpenAIBackend,
    "claude-cli": ClaudeCliBackend,
    "codex-cli": CodexCliBackend,
    "mock": MockBackend,
}

# 主通道 / 被测通道各缓存一个后端实例，避免重复构造
_main_backend: Optional[_BaseBackend] = None
_target_backend: Optional[_BaseBackend] = None

# 不记录 prompt 原文，只记录长度/token/耗时/模型，避免把生产对话二次泄露到遥测文件。
_telemetry_phase: ContextVar[str] = ContextVar("evalcall_telemetry_phase", default="unspecified")
_telemetry_events: list[dict[str, Any]] = []


def _build_backend(name: str, model: Optional[str], *, is_target: bool = False) -> _BaseBackend:
    """按名字构造后端。被测通道复用相同后端类，但读取 TARGET_* 环境变量。"""
    name = (name or "claude-cli").strip().lower()
    if name not in _BACKENDS:
        raise LLMError(f"未知后端 {name!r}，可选：{', '.join(_BACKENDS)}")
    cls = _BACKENDS[name]
    if not is_target:
        return cls(model=model)
    # 被测通道：用 TARGET_ 前缀的环境变量覆盖
    if name == "openai":
        return OpenAIBackend(
            model=model,
            base_url_env="TARGET_BASE_URL",
            key_env="TARGET_API_KEY",
            model_env="TARGET_MODEL",
        )
    if name == "claude-cli":
        return ClaudeCliBackend(model=model, model_env="TARGET_MODEL")
    if name == "codex-cli":
        return CodexCliBackend(
            model=model,
            model_env="TARGET_MODEL",
            effort_env="TARGET_REASONING_EFFORT",
        )
    if name == "mock":
        return MockBackend(model=model, mock_file_env="TARGET_MOCK_FILE")
    return cls(model=model)


def get_backend() -> _BaseBackend:
    """获取（并缓存）主评测通道后端。"""
    global _main_backend
    if _main_backend is None:
        name = os.getenv("EVALCALL_BACKEND", "claude-cli")
        _main_backend = _build_backend(name, model=None)
    return _main_backend


def get_target_backend() -> _BaseBackend:
    """获取（并缓存）被测对话模型通道后端。

    后端类型默认与主通道相同（TARGET_BACKEND 缺省回退 EVALCALL_BACKEND），
    但模型/key/base_url 走 TARGET_* 独立配置。
    """
    global _target_backend
    if _target_backend is None:
        name = os.getenv("TARGET_BACKEND") or os.getenv("EVALCALL_BACKEND", "claude-cli")
        _target_backend = _build_backend(name, model=os.getenv("TARGET_MODEL"), is_target=True)
    return _target_backend


def reset_backends() -> None:
    """清空后端缓存（测试 / 切换环境变量后调用）。"""
    global _main_backend, _target_backend
    _main_backend = None
    _target_backend = None


def reset_telemetry(existing_events: Optional[list[dict[str, Any]]] = None) -> None:
    """清空调用遥测；断点续评可传入旧 events 继续追加。"""
    global _telemetry_events
    _telemetry_events = [dict(e) for e in (existing_events or [])]


@contextmanager
def telemetry_phase(name: str):
    """为调用标记 compile/simulate/judge 等阶段。"""
    token = _telemetry_phase.set(name)
    try:
        yield
    finally:
        _telemetry_phase.reset(token)


def _estimate_tokens(text: str) -> int:
    """无 usage 的 CLI 后端使用透明启发式：汉字按 1 token，其他非空字符每 4 个约 1 token。"""
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    other = len(re.sub(r"[\s\u3400-\u9fff]", "", text))
    return max(1, cjk + math.ceil(other / 4)) if text else 0


def _call_backend(
    backend: Any,
    messages: list[dict],
    *,
    model: Optional[str],
    channel: str,
) -> str:
    """统一后端调用与遥测记录。"""
    started = time.perf_counter()
    input_text = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    try:
        setattr(backend, "_last_usage", None)
    except Exception:  # noqa: BLE001
        pass
    event: dict[str, Any] = {
        "sequence": len(_telemetry_events) + 1,
        "phase": _telemetry_phase.get(),
        "channel": channel,
        "backend": str(getattr(backend, "name", type(backend).__name__)),
        "model": str(model or getattr(backend, "default_model", None) or "unknown"),
        "input_chars": len(input_text),
        "status": "success",
    }
    try:
        output = backend.chat(messages, model=model)
    except Exception as exc:
        event.update(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "input_tokens": _estimate_tokens(input_text),
                "output_tokens": 0,
                "token_source": "estimated",
            }
        )
        event["total_tokens"] = event["input_tokens"]
        _telemetry_events.append(event)
        raise

    actual_model = getattr(backend, "_last_model", None)
    if actual_model:
        event["model"] = str(actual_model)
    usage = getattr(backend, "_last_usage", None)
    if isinstance(usage, dict) and (usage.get("input_tokens") or usage.get("output_tokens")):
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        source = "actual"
    else:
        input_tokens = _estimate_tokens(input_text)
        output_tokens = _estimate_tokens(output)
        source = "estimated"
    event.update(
        {
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "output_chars": len(output),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "token_source": source,
        }
    )
    provider_total = getattr(backend, "_last_provider_total_tokens", None)
    if isinstance(provider_total, int) and provider_total > 0:
        event["provider_total_tokens"] = provider_total
        event["provider_total_source"] = "actual"
    _telemetry_events.append(event)
    return output


def telemetry_snapshot() -> dict[str, Any]:
    events = [dict(event) for event in _telemetry_events]
    phases: dict[str, dict[str, Any]] = {}
    for event in events:
        phase = str(event.get("phase") or "unspecified")
        row = phases.setdefault(
            phase,
            {"calls": 0, "failed_calls": 0, "latency_ms": 0.0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        row["calls"] += 1
        row["failed_calls"] += int(event.get("status") != "success")
        row["latency_ms"] = round(row["latency_ms"] + float(event.get("latency_ms") or 0.0), 1)
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            row[key] += int(event.get(key) or 0)
    total = {
        "calls": len(events),
        "failed_calls": sum(1 for event in events if event.get("status") != "success"),
        "latency_ms": round(sum(float(event.get("latency_ms") or 0.0) for event in events), 1),
        "input_tokens": sum(int(event.get("input_tokens") or 0) for event in events),
        "output_tokens": sum(int(event.get("output_tokens") or 0) for event in events),
        "total_tokens": sum(int(event.get("total_tokens") or 0) for event in events),
        "actual_token_calls": sum(1 for event in events if event.get("token_source") == "actual"),
        "estimated_token_calls": sum(1 for event in events if event.get("token_source") == "estimated"),
        "provider_total_token_calls": sum(1 for event in events if event.get("provider_total_source") == "actual"),
        "provider_total_tokens": sum(int(event.get("provider_total_tokens") or 0) for event in events),
    }
    return {"schema_version": 1, "totals": total, "by_phase": phases, "events": events}


# ----------------------------------------------------------------------------- #
# 对外统一接口
# ----------------------------------------------------------------------------- #
def chat(messages: list[dict], model: Optional[str] = None) -> str:
    """主通道纯文本对话。"""
    return _call_backend(get_backend(), messages, model=model, channel="main")


def target_chat(messages: list[dict], model: Optional[str] = None) -> str:
    """被测对话模型通道纯文本对话（独立后端/模型配置）。"""
    return _call_backend(get_target_backend(), messages, model=model, channel="target")


# JSON 围栏剥离正则：匹配 ```json ... ``` 或 ``` ... ```
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _strip_fence(text: str) -> str:
    """剥掉 ```json``` 代码围栏，返回内部内容；无围栏则原样返回。"""
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _repair_unescaped_quotes(s: str) -> str:
    """修复 JSON 字符串值内部未转义的裸双引号（小模型常见输出缺陷）。

    例如指令原文含「新增"低延迟直播"选项」时，模型会把英文引号原样塞进
    JSON 字符串导致解析失败。启发式：扫描时维护"是否在字符串内"状态，
    字符串内遇到 `"` 时向后看下一个非空白字符——若是 `: , ] }` 之一则视为
    字符串结束定界符，否则视为内容里的裸引号，转义为 `\\"`。
    """
    out: list[str] = []
    in_str = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if not in_str:
            if c == '"':
                in_str = True
            out.append(c)
        elif c == "\\":
            out.append(c)
            if i + 1 < n:
                out.append(s[i + 1])
                i += 1
        elif c == '"':
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j >= n or s[j] in ":,]}":
                in_str = False
                out.append(c)
            else:
                out.append('\\"')
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _extract_json(text: str) -> Any:
    """从文本中尽力提取并解析 JSON。

    依次尝试：直接 parse → 剥围栏后 parse → 截取首个 {...} 或 [...] 片段 parse
    → 对各候选做"裸引号修复"后再 parse。全部失败抛 ValueError。
    """
    candidates: list[str] = []
    stripped = _strip_fence(text)
    candidates.append(stripped)
    candidates.append(text.strip())
    # 截取最外层对象 / 数组
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = stripped.find(open_ch)
        end = stripped.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1])
    # 第二轮兜底：裸引号修复后的候选
    candidates += [_repair_unescaped_quotes(c) for c in list(candidates)]
    for cand in candidates:
        if not cand:
            continue
        try:
            return json.loads(cand)
        except Exception:  # noqa: BLE001
            continue
    raise ValueError("无法从模型输出中解析出 JSON")


def chat_json(
    messages: list[dict],
    schema_hint: str,
    model: Optional[str] = None,
    max_retries: int = 3,
) -> Any:
    """要求模型产出 JSON，解析失败自动重试（最多 max_retries 次）。

    - schema_hint：对期望 JSON 结构的自然语言/示例说明，会注入到 system 提示里
    - 自动剥 ```json``` 围栏并容错截取
    - 返回解析后的 dict 或 list；全部重试失败抛 LLMError
    """
    json_instruction = (
        "你必须只输出合法 JSON，不要任何解释、前后缀或 Markdown 围栏。"
        "JSON 结构要求如下：\n" + schema_hint
    )
    # 把 JSON 指令作为额外 system 消息追加在最前
    sys_msg = {"role": "system", "content": json_instruction}
    work_messages = [sys_msg] + list(messages)

    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            raw = _call_backend(get_backend(), work_messages, model=model, channel="main")
            return _extract_json(raw)
        except Exception as exc:  # noqa: BLE001  —— 解析或调用失败都重试
            last_err = exc
            # 重试时把"上次没给出合法 JSON"反馈给模型，提高成功率
            work_messages = [sys_msg] + list(messages) + [
                {
                    "role": "user",
                    "content": "上次回复不是合法 JSON，请严格只输出 JSON，重新给出结果。",
                }
            ]
            print(f"[llm.chat_json] 第 {attempt}/{max_retries} 次解析失败：{exc}", file=sys.stderr)
            # 失败时打印原始输出片段，便于诊断模型到底回了什么
            try:
                print(f"[llm.chat_json] 原始输出片段：{raw[:800]!r}", file=sys.stderr)
            except NameError:
                pass
    raise LLMError(f"chat_json 连续 {max_retries} 次失败：{last_err}")
