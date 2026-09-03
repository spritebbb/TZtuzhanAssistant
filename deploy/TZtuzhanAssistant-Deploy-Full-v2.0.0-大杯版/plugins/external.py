# -*- coding: utf-8 -*-
"""工具插件：外部桥——Codex CLI / DSH Harness 调用。

工具经确认钩子（needs_confirm=True），用户允许后才执行。
"""
from __future__ import annotations

PLUGIN_META = {
    "name": "外部 Agent 桥",
    "version": "1.0.0",
    "description": "codex_run / dsh_run：调用本机 Codex CLI 与 DeepSeek Harness",
    "author": "tuzhan",
}

import asyncio
import os
import re
import tempfile
from pathlib import Path

from backend.core.config import PROJECT_ROOT, config
from backend.core.log import logger
from backend.tools.base import ToolRegistry

# Codex CLI 候选根目录（实际路径按版本目录自动探测，避免写死版本号）
_CODEX_BIN_DIR = Path(os.path.expandvars(r"%LOCALAPPDATA%\OpenAI\Codex\bin"))

# DSH CLI 默认路径：优先读环境变量 DSH_CLI，其次 DSH_ROOT 推导，最后兜底到
# 相对 PROJECT_ROOT 的兄弟目录，避免把开发者本机 D:\DSH 写死（跨机器失效）。
_DSH_CLI_DEFAULT = Path(
    os.getenv(
        "DSH_CLI",
        os.path.join(os.getenv("DSH_ROOT", str(PROJECT_ROOT.parent)), "deepseek-harness", "apps", "cli", "lib", "bin.js"),
    )
).resolve()

# 当前工作目录（DSH 项目根；可用 DSH_CWD 环境变量覆盖）
_DSH_CWD = os.getenv("DSH_CWD", os.getenv("DSH_ROOT", str(PROJECT_ROOT.parent)))


def _codex_path() -> str:
    """Codex CLI 路径：环境变量优先，否则自动探测 bin 目录下最新版。"""
    if config.agent_codex_path:
        return config.agent_codex_path
    try:
        if _CODEX_BIN_DIR.is_dir():
            exes = sorted(
                (p for p in _CODEX_BIN_DIR.rglob("codex.exe") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if exes:
                return str(exes[0])
    except OSError:
        pass
    return str(_CODEX_BIN_DIR / "codex.exe")


def _dsh_path() -> str:
    """DSH CLI 路径：环境变量优先，默认路径存在则用，否则尝试 PATH 中的 dsh。"""
    if config.agent_dsh_cli:
        return config.agent_dsh_cli
    if _DSH_CLI_DEFAULT.exists():
        return str(_DSH_CLI_DEFAULT)
    try:
        import shutil

        found = shutil.which("dsh")
        if found:
            return found
    except Exception:
        pass
    return str(_DSH_CLI_DEFAULT)


def _codex_env() -> dict:
    """Codex 子进程环境：显式指定 CODEX_HOME，必要时注入 DeepSeek key。"""
    env = os.environ.copy()
    codex_home = Path.home() / ".codex"
    if codex_home.is_dir():
        env.setdefault("CODEX_HOME", str(codex_home))
    if "DEEPSEEK_API_KEY" not in env:
        # deepseek profile 通过 DEEPSEEK_API_KEY 鉴权；若主 LLM 本身就是
        # DeepSeek（.env 的 LLM_API_KEY），直接复用，避免用户重复配置
        if "deepseek" in (config.llm_base_url or "").lower() and config.llm_api_key:
            env["DEEPSEEK_API_KEY"] = config.llm_api_key
    return env


def _codex_final_answer(out_file: Path, stdout: str) -> str:
    """优先取 --output-last-message 写入的最终回复，其次从 stdout 提取。"""
    if out_file is not None and out_file.is_file():
        try:
            text = out_file.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
        except OSError:
            pass
    # 兜底：去掉 ANSI 颜色码与 "tokens used" 计数行
    text = re.sub(r"\x1b\[[0-9;]*m", "", stdout or "")
    lines = [ln for ln in text.splitlines() if ln.strip() and "tokens used" not in ln]
    return "\n".join(lines).strip()


def _clean_stderr(err: str, limit: int = 400) -> str:
    """清洗外部 CLI 的 stderr：过滤已知噪音行并截断（结果由调用方标注）。"""
    lines = []
    for ln in (err or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        low = s.lower()
        if "tokens used" in low or any(ch in s for ch in "⠙⠹⠼⠇⠧"):
            continue
        lines.append(s)
    return "\n".join(lines).strip()[:limit]


async def _codex_run(prompt: str = "") -> str:
    """通过 Codex CLI（非交互 exec 模式）执行一次独立任务。"""
    if not prompt:
        return "（缺少 prompt）"
    codex = _codex_path()
    if not Path(codex).exists():
        return f"（Codex CLI 未找到：{codex}。请设置 AGENT_CODEX_PATH 环境变量）"
    profile = config.agent_codex_profile or "deepseek"
    cwd = config.agent_codex_cwd or str(PROJECT_ROOT)
    timeout = config.agent_codex_timeout
    out_file: Path | None = None
    proc = None
    try:
        # --output-last-message 让 Codex 把最终回复单独写入文件，避开终端日志噪音
        fd, out_path = tempfile.mkstemp(prefix="codex_out_", suffix=".txt")
        os.close(fd)
        out_file = Path(out_path)
        # Windows CreateProcess 命令行上限约 32K，且转义会使引号/空格密集的
        # 内容膨胀（固定阈值无法准确预估）——统一经 stdin 传任务描述最稳：
        # Codex CLI 在未提供 PROMPT 参数时支持从 stdin 读取。
        proc = await asyncio.create_subprocess_exec(
            codex, "exec", "-p", profile,
            "--skip-git-repo-check", "-C", cwd,
            "--ephemeral", "--color", "never",
            "--output-last-message", str(out_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            env=_codex_env(),
            cwd=cwd if Path(cwd).is_dir() else None,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode("utf-8")), timeout=timeout
        )
        out = _codex_final_answer(out_file, stdout.decode("utf-8", errors="replace"))
        err = stderr.decode("utf-8", errors="replace") if stderr else ""
        if err:
            err_clean = _clean_stderr(err, 500)
            if err_clean:
                label = "（stderr，非任务输出；仅提示，不代表任务结果）"
                out += f"\n{label}: {err_clean}" if out else f"{label}: {err_clean}"
        return out or "（Codex 执行完毕，无输出）"
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return f"（Codex 执行超时（{timeout}s），已终止）"
    except FileNotFoundError:
        return "（Codex CLI 未找到：请确保已安装或设置 AGENT_CODEX_PATH）"
    except Exception as e:
        return f"（Codex 执行失败：{e}）"
    finally:
        if out_file is not None:
            try:
                out_file.unlink(missing_ok=True)
            except OSError:
                pass


async def _dsh_run(task: str = "") -> str:
    """通过 DeepSeek Harness CLI 执行一次任务。"""
    if not task:
        return "（缺少 task）"
    # DSH headless 的任务以位置参数经 argv 传给 CLI（其命令契约未提供 stdin 读取），
    # Windows CreateProcess 命令行上限约 32K——先做长度预检并给出明确错误，
    # 避免启动失败时只抛通用异常
    if len(task) > 20000:
        return (
            f"（任务过长（{len(task)} 字符）：DSH headless 经位置参数接收任务，"
            "超出 Windows 命令行约 32K 上限，请把任务拆分后再试）"
        )
    dsh = _dsh_path()
    profile = config.agent_dsh_profile or "headless"
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", dsh, "--profile", profile, task,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_DSH_CWD if Path(_DSH_CWD).is_dir() else None,
        )
        timeout = int(getattr(config, "agent_dsh_timeout", 120))
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        out = stdout.decode("utf-8", errors="replace") if stdout else ""
        err = stderr.decode("utf-8", errors="replace") if stderr else ""
        if err:
            err_clean = _clean_stderr(err, 300)
            if err_clean:
                label = "（stderr，非任务输出；仅提示，不代表任务结果）"
                out += f"\n{label}: {err_clean}" if out else f"{label}: {err_clean}"
        return out or "（DSH 执行完毕，无输出）"
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        timeout = int(getattr(config, "agent_dsh_timeout", 120))
        return f"（DSH 执行超时（{timeout}s），已终止）"
    except FileNotFoundError:
        return "（Node.js 未在 PATH 中找到）"
    except Exception as e:
        return f"（DSH 执行失败：{e}）"


def register(ctx=None) -> None:
    ToolRegistry.register_func(
        name="codex_run",
        description="通过本机 Codex CLI（非交互 exec 模式）执行一次独立任务，适用于需要独立上下文工作的场景",
        func=_codex_run,
        owner="external",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "要 Codex 执行的任务描述"}
            },
            "required": ["prompt"],
        },
        category="external",
        danger_level="high",
        needs_confirm=True,
    )
    ToolRegistry.register_func(
        name="dsh_run",
        description="通过 DeepSeek Harness CLI 执行一次任务，适用于需要 DSH 能力的场景",
        func=_dsh_run,
        owner="external",
        input_schema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "要 DSH 执行的任务描述"}
            },
            "required": ["task"],
        },
        category="external",
        danger_level="high",
        needs_confirm=True,
    )

    # 系统提示注入：让菟菚"知道"自己有外部 Agent 桥，且必须真调而不是嘴上说。
    # （没有这段时她会说"我去调一下"却不发起工具调用——审计日志曾证实 0 次真实调用。）
    ctx.on_system_prompt(lambda: (
        "- 你带有外部 Agent 桥工具：codex_run（调用本机 Codex CLI 执行独立任务）、"
        "dsh_run（调用 DeepSeek Harness 执行任务）。\n"
        "- 当用户要求「用 Codex / 让 Codex 做 / 调 DSH / 交给外面的 AI」"
        "或任务适合外包给独立执行者时，**必须当场发起对应的工具调用**——"
        "绝不允许只回一句「我看看」「我翻一下」「稍等我调」却不调用任何工具。\n"
        "- 调用会弹确认卡片请用户放行，这是正常流程，直接发起即可。\n"
        "- 调用后基于工具真实返回的结果汇报；失败就如实说失败原因，不编造执行结果。"
    ))
