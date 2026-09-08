# -*- coding: utf-8 -*-
"""内置能力演示（Agent 教程）：一份可直接点击执行的演示脚本。

设计目标（2026-09-09 用户要求「侧重展现 Agent 能力」）：

- 每一步都是一句**用户可以照原话发出去**的话，发出去就该看到对应能力真实发生
  （不是口头介绍）；
- 顺序从日常到硬核：记忆 → 联网 → 动手 → **并行子代理** → **浏览器自动化** →
  查文档 → 任务面板；第 4、5 步是 Agent 能力的主秀；
- 每步标注它展示什么、用哪些工具、以及「没看到就等于没生效」的验收点，
  便于演示时一眼判断是否真的跑了；
- 与 ``skills/agent-tour.md`` 配套：用户说「新手教程/演示」时技能会注入，
  菟菚按同一顺序逐步演示；本模块把这份脚本变成结构化数据供前端/测试使用。
"""
from __future__ import annotations

# 每步：id / 标题 / 展示什么 / 建议原话 / 涉及工具 / 验收点
TOUR_STEPS: tuple[dict, ...] = (
    {
        "id": "memory",
        "title": "长期记忆",
        "shows": "她说记住就真的记得住，下次还查得到",
        "prompt": "记住一件事：我明天要去看牙医。",
        "follow_up": "我明天要干嘛来着？",
        "tools": ["memory_add", "memory_search"],
        "check": "第二句能准确答出「看牙医」，而不是泛泛而谈",
    },
    {
        "id": "web",
        "title": "联网查实时信息",
        "shows": "拿的是今天的数据，不是训练时的旧知识",
        "prompt": "查一下今天武汉的天气。",
        "tools": ["web_search"],
        "check": "回答里带当天真实的温度/天气，而不是「我无法获取实时信息」",
    },
    {
        "id": "files",
        "title": "动手操作本机",
        "shows": "能真的读写文件、跑命令，不只是聊天",
        "prompt": "在工作区建一个 demo.txt，写上今天的日期，再读回来念给我听。",
        "tools": ["write_file", "read_file"],
        "check": "工作区出现 demo.txt，且她能念出里面的内容",
    },
    {
        "id": "fanout",
        "title": "并行子代理（Agent 核心）",
        "shows": "一次派发多个子代理分头干活，再汇总成结论",
        "prompt": "帮我比较一下 Python 和 Node.js 做后端，从性能、生态、学习成本三个方面分析。",
        "tools": ["agent_fanout"],
        "check": "工具面板出现 agent_fanout，且结论是按三个维度横向比较的，不是串行罗列",
    },
    {
        "id": "browser",
        "title": "浏览器自动化（MCP）",
        "shows": "真的开浏览器打开页面、读内容、截图",
        "prompt": "用浏览器打开 example.com，看看页面上写了什么。",
        "tools": ["browser_navigate", "browser_snapshot"],
        "check": "弹出浏览器窗口，且她能说出页面上的「Example Domain」",
    },
    {
        "id": "docs",
        "title": "查实时文档（MCP）",
        "shows": "查官方文档回答用法，不靠记忆里的旧版本",
        "prompt": "帮我查一下 fastapi 的文档，怎么定义一个带查询参数的路由？",
        "tools": ["resolve-library-id", "query-docs"],
        "check": "答案里给出可运行的路由示例，且引用了文档来源",
    },
    {
        "id": "watch",
        "title": "监控 Agent（盯着页面）",
        "shows": "让她替你盯着某个网页，变了主动告诉你",
        "prompt": "帮我盯着 https://example.com 这个页面，有变化告诉我。",
        "tools": ["watch_add", "watch_list"],
        "check": "watch_list 里出现该地址；页面变化后她会主动开口",
    },
    {
        "id": "schedule",
        "title": "定时任务（到点自己做）",
        "shows": "交代的事不用你催，到点自动执行",
        "prompt": "帮我分几步整理这周的会议纪要，30 分钟后执行。",
        "tools": ["agent_fanout", "todo_create"],
        "check": "任务面板出现该任务且标记「已定时」，到点自动开跑",
    },
    {
        "id": "todo",
        "title": "任务面板",
        "shows": "交代的事会变成可跟进的任务",
        "prompt": "帮我建个待办：本周内整理一份 Agent 演示清单。",
        "tools": ["todo_create"],
        "check": "任务面板里出现这条待办",
    },
)


def get_tour() -> dict:
    """返回演示脚本（供 API / 前端渲染，供测试校验）。"""
    return {
        "title": "菟菚能力演示",
        "intro": "想看我会什么？说「新手教程」我就一步步做给你看；也可以直接发下面任意一句。",
        "skill": "agent-tour",
        "steps": [dict(step) for step in TOUR_STEPS],
    }
