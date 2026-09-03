# -*- coding: utf-8 -*-
"""工具插件：代码执行（本机权限，非隔离沙箱）和系统命令。"""
from __future__ import annotations

PLUGIN_META = {
    "name": "代码执行",
    "version": "1.0.0",
    "description": "run_python（等同本机权限，非隔离沙箱）/ run_command（安全限制）工具",
    "author": "tuzhan",
}

import asyncio
import os
import sys
import traceback

from backend.tools.base import ToolRegistry, tool_failure
from backend.tools.safety import check_command, check_cwd


async def _run_python(code: str = "") -> str:
    """执行 Python 代码（以本机当前用户权限运行，等同本机权限）。

    诚实声明：这不是隔离沙箱——代码运行在拥有本机权限的 Python 子进程里，
    反射/逃逸链无法被黑名单完全封死，等同本机权限。真正的高危操作由确认钩子
    把关（用户看到上述声明后授权）。代码执行前做静态扫描：
    - 禁止 import 危险模块
    - 尝试拦截常见反射逃逸属性（__class__/__subclasses__ 等，以及裸别名
      mro/subclasses/fget/gi_frame/func_globals……）——但这是"有限拦截"：
      type/getattr 配合不带双下划线的合法别名即可绕过（如
      getattr(type(1), "mro")），黑名单式防护不保证封死，仅防误操作
    - 执行时移除危险内置函数
    执行在子进程中进行（上限 60s，超时 kill）：
    - 死循环/长计算不再冻结事件循环（旧实现 exec 同步跑在 loop 线程）
    - 子进程隔离 stdout 重定向，避免污染主进程的 sys.stdout
    """
    if not code:
        return tool_failure("（缺少代码）")
    # 静态扫描：禁止危险模块导入 + 反射逃逸链
    import ast

    # 反射逃逸链上的危险属性名（访问即拒绝）
    _FORBIDDEN_ATTRS = {
        "__class__", "__bases__", "__mro__", "__subclasses__",
        "__globals__", "__builtins__", "__builtin__", "__loader__",
        "__init__", "__dict__", "__code__", "__getattribute__",
    }
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _RESTRICTED_MODULES or any(alias.name.startswith(m + ".") for m in _RESTRICTED_MODULES):
                        return tool_failure(f"（不允许使用 {alias.name} 模块）")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module in _RESTRICTED_MODULES or any(node.module.startswith(m + ".") for m in _RESTRICTED_MODULES)):
                    return tool_failure(f"（不允许使用 {node.module} 模块）")
            elif isinstance(node, ast.Attribute):
                if node.attr in _FORBIDDEN_ATTRS:
                    return tool_failure(f"（不允许访问 {node.attr}（反射逃逸防护））")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
                # getattr(obj, "__xxx__") 动态反射同样拦截：
                # 第二参数为字符串字面量且命中名单 → 拒；
                # 第二参数为非常量（拼接/变量，如 "__cla"+"ss__"）→ 一律拒绝（宁可错杀）
                if len(node.args) >= 2:
                    if isinstance(node.args[1], ast.Constant):
                        if isinstance(node.args[1].value, str) and node.args[1].value in _FORBIDDEN_ATTRS:
                            return tool_failure(f"（不允许 getattr 访问 {node.args[1].value}）")
                    else:
                        return tool_failure("（不允许动态 getattr（反射逃逸防护））")
    except SyntaxError as e:
        return tool_failure(f"（代码语法错误：{e}）")

    return await _exec_in_subprocess(code)


_RUN_PY_TIMEOUT = 60  # 子进程执行上限（秒）


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """终止子进程及其孙进程（Windows 用 taskkill /T，其他平台 kill 进程组）。"""
    try:
        if os.name == "nt":
            # /T 会连同子进程树一起结束；/F 强制。用 taskkill 而非 proc.kill，
            # 否则子进程若再 spawn 孙进程会残留孤儿进程。
            kill = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await kill.wait()
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        await proc.wait()
    except Exception:
        pass


async def _exec_in_subprocess(code: str) -> str:
    """在受限子进程中执行代码（超时 kill，不阻塞事件循环）。

    代码经 stdin 传入（避免命令行长度/转义问题），子进程内用白名单
    builtins 的受限 exec 运行（见 _RUN_CHILD_SRC），stdout/stderr 走管道。
    """
    import json as _json

    wrapper = (
        "import sys, json\n"
        f"exec(compile({_RUN_CHILD_SRC!r}, '<sandbox>', 'exec'), {{'__name__': '__main__'}})\n"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", wrapper,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        payload = _json.dumps({"code": code})
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(payload.encode("utf-8")), timeout=_RUN_PY_TIMEOUT)
        except asyncio.TimeoutError:
            await _kill_tree(proc)
            return tool_failure(f"（代码执行超时（>{_RUN_PY_TIMEOUT}s），已终止）")
        except asyncio.CancelledError:
            await _kill_tree(proc)
            raise
        out = stdout.decode("utf-8", errors="replace") if stdout else ""
        err = stderr.decode("utf-8", errors="replace")[:500] if stderr else ""
        if len(out) > 5000:
            out = out[:2500] + f"\n…（输出过长，中间省略 {len(out) - 5000} 字符）…\n" + out[-2500:]
        if err and err.strip():
            out = (out + f"\n（stderr: {err.strip()}）") if out else f"（stderr: {err.strip()}）"
        if proc.returncode:
            return tool_failure(out or f"（代码执行失败，退出码 {proc.returncode}）")
        return out or "（执行成功，无输出）"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return tool_failure(f"（执行失败：{e}）")


# 子进程内的受限执行模板（白名单 builtins + io 捕获，JSON stdin 传 code）
_RUN_CHILD_SRC = (
    "import sys, io, json, traceback as _tb\n"
    "_SAFE = ['__build_class__','__name__','abs','all','any','bool','bytes','chr','dict',"
    "'divmod','enumerate','filter','float','format','frozenset','hash','hex','int',"
    "'isinstance','issubclass','iter','len','list','map','max','min','oct','ord','pow',"
    "'print','range','repr','reversed','round','set','slice','sorted','str','sum','tuple','zip']\n"
    "_b = sys.modules['builtins']\n"
    "safe = {k: getattr(_b, k) for k in _SAFE if hasattr(_b, k)}\n"
    "code = json.load(sys.stdin)['code']\n"
    "buf = io.StringIO()\n"
    "_old = sys.stdout\n"
    "sys.stdout = buf\n"
    "try:\n"
    "    g = {'__builtins__': safe}\n"
    "    exec(code, g, g)\n"
    "    out = buf.getvalue()\n"
    "    sys.stdout = _old\n"
    "    print(out if out.strip() else '（执行成功，无输出）')\n"
    "except BaseException as e:\n"
    "    sys.stdout = _old\n"
    "    print(f'（执行错误：{e}\\n{_tb.format_exc()[:500]}）')\n"
    "    raise SystemExit(1)\n"
)


_RESTRICTED_MODULES = (
    "os", "subprocess", "shutil", "socket", "ctypes", "importlib",
    "multiprocessing", "threading", "signal", "code", "codeop",
    "pty", "fcntl", "mmap", "crypt", "grp", "pwd",
)

# Windows cmd 内建命令（没有独立 .exe，CreateProcess 直接启动必然 FileNotFoundError；
# 命中这些命令时改经 cmd /c 执行）
_WIN_CMD_BUILTINS = {
    "assoc", "break", "call", "cd", "chdir", "cls", "color", "copy", "date",
    "del", "dir", "echo", "endlocal", "erase", "exit", "for", "ftype", "goto",
    "if", "md", "mkdir", "mklink", "move", "path", "pause", "popd", "prompt",
    "pushd", "rd", "rem", "ren", "rename", "rmdir", "set", "setlocal", "shift",
    "start", "time", "title", "type", "ver", "verify", "vol", "where",
}


def _cmd_builtin(command: str) -> bool:
    """判断命令是否命中 Windows cmd 内建（取首个 token，不处理引号内的空格场景）。"""
    head = (command or "").lstrip().split(None, 1)[0].lower() if (command or "").strip() else ""
    return head in _WIN_CMD_BUILTINS


async def _run_command(command: str = "", cwd: str = "") -> str:
    """执行系统命令（subprocess 列表参数，避免 shell 注入）。"""
    if not command:
        return tool_failure("（缺少命令）")
    # 1) 安全黑名单检查
    ok, err = check_command(command)
    if not ok:
        return tool_failure(err)
    # 2) cwd 白名单检查
    cwd_ok, cwd_err = check_cwd(cwd)
    if not cwd_ok:
        return tool_failure(cwd_err)
    # 3) 解析命令为列表参数（避免 shell 注入）
    import shlex
    try:
        args = shlex.split(command, posix=False)
    except ValueError as e:
        return tool_failure(f"（命令解析失败：{e}）")
    if not args:
        return tool_failure("（空的命令）")
    # Windows：cmd 内建命令（dir/type/cd/echo 等）不存在独立 exe，
    # 工具示例里的 `dir /w` 直接 CreateProcess 会 100% FileNotFoundError。
    # 命中内建表时改经 `cmd /c <原命令>` 执行（黑名单在进入本分支前已检查）。
    if os.name == "nt" and _cmd_builtin(command):
        args = ["cmd", "/c", command]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        if stderr:
            err = stderr.decode("utf-8", errors="replace")[:500]
            if output:
                output += f"\n（stderr: {err}）"
            else:
                output = f"（stderr: {err}）"
        if proc.returncode:
            return tool_failure(output or f"（命令执行失败，退出码 {proc.returncode}）")
        if not output:
            return "（命令执行成功，无输出）"
        # 结果截断（超过 5000 字符保留头尾）
        if len(output) > 5000:
            output = output[:2500] + f"\n…（输出过长，中间省略 {len(output) - 5000} 字符）…\n" + output[-2500:]
        return output
    except asyncio.TimeoutError:
        # 超时只取消 await 不会终止子进程，这里显式 kill（含孙进程），避免孤儿进程残留
        await _kill_tree(proc)
        return tool_failure("（命令执行超时，已终止）")
    except asyncio.CancelledError:
        if proc is not None:
            await _kill_tree(proc)
        raise
    except FileNotFoundError:
        return tool_failure("（命令未找到：请检查命令是否可用）")
    except Exception as e:
        return tool_failure(f"（命令执行失败：{e}）")


def register(ctx=None) -> None:
    ToolRegistry.register_func(
        name="run_python",
        description=(
            "执行任意 Python 代码——在本机以当前用户权限运行，等同本机权限，"
            "非隔离沙箱（仅有限危险模块/反射拦截）。需用户确认后执行。"
        ),
        func=_run_python,
        owner="code_exec",
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python 代码"}
            },
            "required": ["code"],
        },
        category="run",
        danger_level="high",
        needs_confirm=True,
    )
    ToolRegistry.register_func(
        name="run_command",
        description="执行系统命令（subprocess 列表参数，避免 shell 注入；有安全限制）",
        func=_run_command,
        owner="code_exec",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令（如 dir /w）"},
                "cwd": {"type": "string", "description": "工作目录（可选，必须在允许目录内）"}
            },
            "required": ["command"],
        },
        category="run",
        danger_level="high",
        needs_confirm=True,
    )
