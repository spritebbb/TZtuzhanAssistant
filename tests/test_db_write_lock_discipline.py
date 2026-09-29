# -*- coding: utf-8 -*-
"""共享连接写纪律守卫（DF-2）：db.conn.commit() 必须在持锁段内。

db.conn 是全库共享的单一 SQLite 连接；任何一个模块在 `with db._lock` 之外
commit，都会把其他线程正持锁执行的多语句事务（级联删除/冲突解决/reset 大
事务）的半截内容提前提交——把本可回滚的操作变成半删状态。2026-09-29 缺陷
审查在 schedule._record_event 与 pending_thoughts.sync 尾部各发现一处，已修；
本守卫用 AST 全仓扫描防止回归。

规则：`db.conn.commit()` 调用行必须落在
  a) `with db._lock:` 块内，或
  b) `@_locked` 装饰的函数体内，或
  c) 下方 UNLOCKED_OK 的显式豁免（须附理由，随架构演进手工维护）。
backend/core/userdb.py 是锁宿主自身（方法体由 @_locked 约束），不扫描。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (文件相对路径, 行号, 理由)——新增豁免必须走代码评审
UNLOCKED_OK: list[tuple[str, int, str]] = []


def _commit_lines(tree: ast.Module) -> set[int]:
    out: set[int] = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "commit"
                and isinstance(n.func.value, ast.Attribute)
                and n.func.value.attr == "conn"
                and isinstance(n.func.value.value, ast.Name)
                and n.func.value.value.id == "db"):
            out.add(n.lineno)
    return out


def _locked_lines(tree: ast.Module) -> set[int]:
    out: set[int] = set()

    class Visitor(ast.NodeVisitor):
        def visit_With(self, node: ast.With) -> None:
            for item in node.items:
                ctx = item.context_expr
                if (isinstance(ctx, ast.Attribute) and ctx.attr == "_lock"
                        and isinstance(ctx.value, ast.Name) and ctx.value.id == "db"):
                    for n in ast.walk(node):
                        if hasattr(n, "lineno"):
                            out.add(n.lineno)
                    break
            self.generic_visit(node)

        visit_AsyncWith = visit_With

        def visit_FunctionDef(self, node) -> None:
            if any((isinstance(d, ast.Name) and d.id == "_locked")
                   or (isinstance(d, ast.Attribute) and d.attr == "_locked")
                   for d in node.decorator_list):
                for n in ast.walk(node):
                    if hasattr(n, "lineno"):
                        out.add(n.lineno)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

    Visitor().visit(tree)
    return out


def test_no_unlocked_commit() -> int:
    violations: list[str] = []
    for path in sorted((ROOT / "backend").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if path.name == "userdb.py":  # 锁宿主自身
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bad = _commit_lines(tree) - _locked_lines(tree)
        for line in sorted(bad):
            if any(rel == f and line == ln for f, ln, _ in UNLOCKED_OK):
                continue
            violations.append(f"{rel}:{line}")
    assert not violations, (
        "锁外 db.conn.commit()——会把他人持锁事务的半截内容提前提交：" 
        + "、".join(violations)
    )
    print("[OK] 写纪律守卫：全仓 db.conn.commit() 均在持锁段内（userdb.py 宿主除外）")
    return 0


def main() -> int:
    failed = test_no_unlocked_commit()
    if failed:
        print(f"\n=== DF-2 写纪律守卫：{failed} 项失败 ===")
        return 1
    print("\n=== DF-2 写纪律守卫：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
