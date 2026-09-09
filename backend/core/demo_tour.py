# -*- coding: utf-8 -*-
"""内置能力演示（Agent 教程）：一份可直接点击执行的演示脚本。

设计目标（2026-09-09 用户要求「侧重展现 Agent 能力」；同日扩充为分组全能力版）：

- 每一步都是一句**用户可以照原话发出去**的话，发出去就该看到对应能力真实发生
  （不是口头介绍）；界面型步骤（上传图片、点朗读）用 ``kind="ui"`` 标注操作路径；
- 按能力域分组，顺序从日常到硬核：
  记忆与了解 → 联网与信息 → 多模态 → 动手与执行 → Agent 主秀 → 长期陪伴；
  并行子代理、浏览器自动化、任务产物落盘是 Agent 主秀；
- 每步标注它展示什么、用哪些工具/接口、以及「没看到就等于没生效」的验收点，
  便于演示时一眼判断是否真的跑了；有前置条件的步骤用 ``needs`` 如实标注；
- 与 ``skills/agent-tour.md`` 配套：用户说「新手教程/演示」时技能会注入，
  菟菚按同一顺序逐步演示；本模块把这份脚本变成结构化数据供前端/测试使用。
"""
from __future__ import annotations

# 分组顺序 = 演示顺序（前端按此渲染分组标题，测试按此校验）
TOUR_GROUPS: tuple[str, ...] = (
    "记忆与了解",
    "联网与信息",
    "多模态",
    "动手与执行",
    "Agent 主秀",
    "长期陪伴",
)

# 每步：id / 标题 / 展示什么 / 建议原话 / 涉及工具或接口 / 验收点
#     + group（所属分组）/ kind（chat=发一句话；ui=点界面）/ needs（可选前置条件）
TOUR_STEPS: tuple[dict, ...] = (
    {
        "id": "memory",
        "group": "记忆与了解",
        "kind": "chat",
        "title": "长期记忆",
        "shows": "她说记住就真的记得住，下次还查得到",
        "prompt": "记住一件事：我明天要去看牙医。",
        "follow_up": "我明天要干嘛来着？",
        "tools": ["memory_add", "memory_search"],
        "check": "第二句能准确答出「看牙医」，而不是泛泛而谈",
    },
    {
        "id": "web",
        "group": "联网与信息",
        "kind": "chat",
        "title": "联网查实时信息",
        "shows": "拿的是今天的数据，不是训练时的旧知识",
        "prompt": "查一下今天武汉的天气。",
        "tools": ["web_search"],
        "check": "回答里带当天真实的温度/天气，而不是「我无法获取实时信息」",
    },
    {
        "id": "draw",
        "group": "多模态",
        "kind": "chat",
        "title": "生图（她真的画）",
        "shows": "按你的描述现画一张图，不是搜图贴给你",
        "prompt": "帮我画一张猫趴在窗台上晒太阳的图。",
        "tools": ["imagegen"],
        "check": "气泡下方出现新生成的图片，画面内容对得上描述",
    },
    {
        "id": "vision",
        "group": "多模态",
        "kind": "ui",
        "title": "识图（她能看图）",
        "shows": "你把图发给她，她能读出画面内容",
        "prompt": "点输入框左侧的图片按钮，选一张图发出去，再问她「你看到了什么」。",
        "tools": ["/api/vision"],
        "check": "她的回复描述的是图里的真实内容，不是泛泛猜测",
        "needs": "需要配置视觉模型（VISION_*，或复用生图端点）",
    },
    {
        "id": "tts",
        "group": "多模态",
        "kind": "ui",
        "title": "语音朗读",
        "shows": "她能把回复用语音念出来",
        "prompt": "点她任意一条回复气泡上的「朗读」按钮。",
        "tools": ["/api/tts"],
        "check": "浏览器开始播放语音；再次点击可停止",
    },
    {
        "id": "files",
        "group": "动手与执行",
        "kind": "chat",
        "title": "动手操作本机",
        "shows": "能真的读写文件、跑命令，不只是聊天",
        "prompt": "在工作区建一个 demo.txt，写上今天的日期，再读回来念给我听。",
        "tools": ["write_file", "read_file"],
        "check": "工作区出现 demo.txt，且她能念出里面的内容",
    },
    {
        "id": "code",
        "group": "动手与执行",
        "kind": "chat",
        "title": "跑代码算结果",
        "shows": "真执行脚本，把算出来的结果给你（不是口算）",
        "prompt": "跑个脚本算一下 1 到 100 的平方和。",
        "tools": ["run_python"],
        "check": "工具面板出现 run_python，且答案等于 338350",
    },
    {
        "id": "currency",
        "group": "动手与执行",
        "kind": "chat",
        "title": "汇率换算（插件）",
        "shows": "插件式工具：装了就长在她手上，随时可调",
        "prompt": "把 100 美元换算成人民币。",
        "tools": ["currency_convert"],
        "check": "给出换算金额，并说明所用汇率（不是编一个数字）",
    },
    {
        "id": "todo",
        "group": "动手与执行",
        "kind": "chat",
        "title": "任务面板",
        "shows": "交代的事会变成可跟进的任务",
        "prompt": "帮我建个待办：本周内整理一份 Agent 演示清单。",
        "tools": ["todo_create"],
        "check": "任务面板里出现这条待办",
    },
    {
        "id": "fanout",
        "group": "Agent 主秀",
        "kind": "chat",
        "title": "并行子代理（Agent 核心）",
        "shows": "一次派发多个子代理分头干活，再汇总成结论",
        "prompt": "帮我比较一下 Python 和 Node.js 做后端，从性能、生态、学习成本三个方面分析。",
        "tools": ["agent_fanout"],
        "check": "工具面板出现 agent_fanout，且结论是按三个维度横向比较的，不是串行罗列",
    },
    {
        "id": "browser",
        "group": "Agent 主秀",
        "kind": "chat",
        "title": "浏览器自动化（MCP）",
        "shows": "真的开浏览器打开页面、读内容、截图",
        "prompt": "用浏览器打开 example.com，看看页面上写了什么。",
        "tools": ["browser_navigate", "browser_snapshot"],
        "check": "弹出浏览器窗口，且她能说出页面上的「Example Domain」",
        "needs": "需要先启动 MCP 桥（scripts\\start-mcp-playwright.bat）",
    },
    {
        "id": "docs",
        "group": "Agent 主秀",
        "kind": "chat",
        "title": "查实时文档（MCP）",
        "shows": "查官方文档回答用法，不靠记忆里的旧版本",
        "prompt": "帮我查一下 fastapi 的文档，怎么定义一个带查询参数的路由？",
        "tools": ["resolve-library-id", "query-docs"],
        "check": "答案里给出可运行的路由示例，且引用了文档来源",
        "needs": "需要 context7 MCP 服务器已连接",
    },
    {
        "id": "watch",
        "group": "Agent 主秀",
        "kind": "chat",
        "title": "监控 Agent（盯着页面）",
        "shows": "让她替你盯着某个网页，变了主动告诉你",
        "prompt": "帮我盯着 https://example.com 这个页面，有变化告诉我。",
        "tools": ["watch_add", "watch_list"],
        "check": "watch_list 里出现该地址；页面变化后她会主动开口",
    },
    {
        "id": "schedule",
        "group": "Agent 主秀",
        "kind": "chat",
        "title": "定时任务（到点自己做）",
        "shows": "交代的事不用你催，到点自动执行",
        "prompt": "帮我分几步整理这周的会议纪要，30 分钟后执行。",
        "tools": ["agent_fanout", "todo_create"],
        "check": "任务面板出现该任务且标记「已定时」，到点自动开跑",
    },
    {
        "id": "report",
        "group": "Agent 主秀",
        "kind": "chat",
        "title": "多步任务 + 产物落盘",
        "shows": "派活给她：自己拆步骤、逐步执行、把结果存成文件交付",
        "prompt": "帮我分几步整理一份「本周 Agent 演示清单」，做完把结果存成文件。",
        "tools": ["agent_fanout", "write_file"],
        "check": "任务面板出现多步计划；完成后 workspace/agent-reports/ 里有产物文件",
    },
    {
        "id": "mood",
        "group": "长期陪伴",
        "kind": "chat",
        "title": "情绪与关系状态",
        "shows": "她的心情和你们的关系是连续状态，不是每轮重置",
        "prompt": "你现在心情怎么样？我们认识多久了？",
        "tools": ["/api/meta"],
        "check": "回答里带她当前的心情与关系阶段，而不是泛泛而谈",
    },
    {
        "id": "explain",
        "group": "长期陪伴",
        "kind": "ui",
        "title": "可解释性（她为什么这么说）",
        "shows": "每条回复都能展开看她依据了哪些记忆与状态",
        "prompt": "点她回复气泡下方的「为什么」按钮。",
        "tools": ["explanation"],
        "check": "弹出依据面板，列出本轮参考的记忆/状态来源",
    },
    {
        "id": "ephemeral",
        "group": "长期陪伴",
        "kind": "ui",
        "title": "临时对话（不留痕）",
        "shows": "这一轮只存在于当前界面，不写记忆、关系与日记",
        "prompt": "点输入框上方的「本轮不留痕」，再随便聊一句；刷新后这轮就没了。",
        "tools": ["ephemeral"],
        "check": "气泡上出现「不留痕」标记，刷新后该轮不再出现",
    },
)


def get_tour() -> dict:
    """返回演示脚本（供 API / 前端渲染，供测试校验）。"""
    return {
        "title": "菟菚能力演示",
        "intro": "想看我会什么？说「新手教程」我就一步步做给你看；也可以直接发下面任意一句。",
        "skill": "agent-tour",
        "groups": list(TOUR_GROUPS),
        "steps": [dict(step) for step in TOUR_STEPS],
    }
