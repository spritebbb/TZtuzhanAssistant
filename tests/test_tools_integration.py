# -*- coding: utf-8 -*-
"""核心工具集成测试（真实文件/命令操作，非 mock）。

覆盖：file_ops、file_edit、file_search、run_command、code_exec
测试在项目临时目录中进行，运行后自动清理。

v2：工具已插件化——通过插件系统加载后取插件模块里的实现函数。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import importlib

from backend.plugins import loader
from backend.tools.builtin.register_all import register_all


def _plugin(name: str):
    """加载全部插件并返回对应插件模块。"""
    register_all()
    if loader.load_plugin(loader.PLUGINS_DIR / f"{name}.py") is None:
        raise RuntimeError(f"插件加载失败: {name} -> {loader.plugin_states().get(name)}")
    return importlib.import_module(f"plugin_{name}")


_file_ops = _plugin("file_ops")
_file_edit = _plugin("file_edit")
_file_search = _plugin("file_search")
_code_exec = _plugin("code_exec")

write_file, read_file, list_dir = _file_ops._write_file, _file_ops._read_file, _file_ops._list_dir
edit_file = _file_edit._edit_file
grep_text, search_files = _file_search._grep, _file_search._glob
code_exec, run_command_impl = _code_exec._run_python, _code_exec._run_command

from backend.tools.safety import check_path, check_command, check_cwd
from backend.core.config import config


def _ensure_temp() -> Path:
    p = config.data_dir / "inttest"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def test_file_ops():
    """文件读写 / 列表操作（真实目录）。"""
    tmp = _ensure_temp()
    f = tmp / "hello.txt"

    # 写
    r = await write_file(path=str(f), content="Hello World\n第二行\n第三行")
    assert "已写入" in r, f"写文件返回异常: {r}"
    assert f.exists()

    # 读
    r = await read_file(str(f))
    assert "Hello World" in r
    assert "第二行" in r

    # 路径安全：拒绝越界
    bad = "C:\\Windows\\System32\\test.txt"
    r = await write_file(path=bad, content="x")
    assert "不允许" in r, f"越界写入应拒绝: {r}"

    # 列表
    r = await list_dir(str(tmp))
    assert "hello.txt" in r

    # 清理
    f.unlink(missing_ok=True)
    print("[OK] 文件读写/列表/越界拦截")


async def test_file_edit():
    """文件编辑（替换 / 行级操作）。"""
    tmp = _ensure_temp()
    f = tmp / "edit.txt"
    f.write_text("第一行\n第二行\n第三行", encoding="utf-8")

    # 替换
    r = await edit_file(path=str(f), old_string="第二行", new_string="替换行")
    assert "成功" in r or "已" in r, f"编辑返回异常: {r}"
    assert "替换行" in f.read_text(encoding="utf-8")

    # 行级调整
    r = await edit_file(path=str(f), old_string="第一行", new_string="开头行")
    assert "成功" in r or "已" in r
    content = f.read_text(encoding="utf-8")
    assert content.startswith("开头行"), f"编辑后内容异常: {content}"

    f.unlink(missing_ok=True)
    print("[OK] 文件编辑（替换/行级）")


async def test_file_search():
    """文件搜索 / 文本 grep。"""
    tmp = _ensure_temp()
    # 创建多个搜索文件
    (tmp / "alpha.txt").write_text("apple banana cherry", encoding="utf-8")
    (tmp / "beta.txt").write_text("banana durian", encoding="utf-8")
    (tmp / "gamma.txt").write_text("cherry elderberry", encoding="utf-8")

    # grep_text: 搜索关键词（限定在临时目录）
    r = await grep_text(pattern="banana", path=str(tmp))
    assert "alpha.txt" in r, f"grep 未找到 alpha.txt: {r}"
    assert "beta.txt" in r
    assert "gamma.txt" not in r, "gamma 不应包含 banana"

    # search_files: 按通配符
    r = await search_files(pattern="*.txt", path=str(tmp))
    assert "alpha.txt" in r
    assert "gamma.txt" in r

    # 清理
    for p in tmp.glob("*.txt"):
        p.unlink(missing_ok=True)
    print("[OK] 文件搜索（grep/通配符）")


async def test_run_command():
    """命令执行 + 安全拦截。"""
    # 安全命令（Windows: echo 是 cmd 内建，需经 cmd /c；用实际可执行命令）
    r = await run_command_impl('cmd /c echo hello from tuzhan')
    assert "hello from tuzhan" in r, f"命令执行失败: {r}"

    # CWD 安全检测
    ok, err = check_cwd("D:\\Windows\\System32")
    assert ok is False, f"CWD 检测应拒绝系统目录: {err}"
    assert "不允许" in err or "不" in err, f"CWD 拒绝理由异常: {err}"

    # 拦截格式化命令
    ok, err = check_command("format c: /q")
    assert ok is False, "format c: 应被拦截"
    ok, err = check_command("shutdown /s /t 0")
    assert ok is False, "shutdown 应被拦截"
    ok, err = check_command("dir")
    assert ok is True, "dir 不应被拦截"

    print("[OK] 命令执行 + 安全拦截")


async def test_code_exec():
    """Python 代码执行。"""
    # 简单表达式（代码执行捕获 stdout，需要用 print 输出）
    r = await code_exec("print(2 + 3 * 4)")
    assert "14" in r, f"代码执行失败: {r}"

    # 变量
    r = await code_exec("x = 10; y = 20; print(x + y)")
    assert "30" in r

    # 空输入
    r = await code_exec("")
    assert "缺少" in r or "no" in r.lower() or not r

    print("[OK] 代码执行（表达式/变量/空输入）")


async def main():
    await test_file_ops()
    await test_file_edit()
    await test_file_search()
    await test_run_command()
    await test_code_exec()
    print("\n=== 核心工具集成测试: 5 项全部通过 ===")


asyncio.run(main())