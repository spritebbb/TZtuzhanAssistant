# -*- coding: utf-8 -*-
"""实测运行中后端的 external 插件：注册状态 / MCP 暴露面 / 真实调用记录。"""
import json
import urllib.request


def get(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕代理直连本机
    with opener.open(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


# 1) 插件注册状态
plugins = get("http://127.0.0.1:8801/api/plugins")["plugins"]
ext = next((p for p in plugins if p["name"] == "external"), None)
print("== 插件状态 ==")
if ext:
    print(f"  显示名: {ext['display_name']}  v{ext['version']}")
    print(f"  状态: {'已加载' if ext['loaded'] else ('已禁用' if ext['disabled'] else '失败')}")
    print(f"  注册工具: {ext['tools']}")
    print(f"  钩子: {ext['hooks']}")
    print(f"  描述: {ext['description']}")
else:
    print("  !! external 插件未找到")

# 2) MCP 暴露面（外部系统实际能看到的工具清单）
print("== MCP /mcp/tools 暴露 ==")
try:
    data = get("http://127.0.0.1:8801/mcp/tools")
    tools = data["tools"] if isinstance(data, dict) else data
    names = [t.get("name", "") for t in tools if isinstance(t, dict)]
    hits = [n for n in names if "codex" in n or "dsh" in n]
    print(f"  工具总数: {len(names)}  桥接相关: {hits}")
except Exception as e:
    print(f"  查询失败: {e}")

# 3) 审计日志：菟菚有没有真的调用过桥接工具
print("== 审计日志（codex_run / dsh_run）==")
try:
    rows = get("http://127.0.0.1:8801/api/audit/log?limit=500").get("rows", [])
    hits = [r for r in rows if r.get("tool") in ("codex_run", "dsh_run")]
    if not hits:
        print(f"  最近 {len(rows)} 条调用里 0 次桥接调用 —— 菟菚确实一次都没真调")
    for r in hits[:5]:
        print(f"  {r['ts'][:19]}  {r['tool']}  confirmed={r['confirmed']}  ok={r['ok']}  {r.get('error', '')[:60]}")
except Exception as e:
    print(f"  查询失败: {e}")
