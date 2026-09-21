# -*- coding: utf-8 -*-
"""测试隔离兜底：没有显式指定数据目录时，绝不落到真实 data/。

多数脚本式用例自己 ``setdefault("TZTUZHAN_DATA_DIR", mkdtemp())``，但 pytest
直接收集的 test_edge_regressions / test_prompt_injection_matrix /
test_resource_authorization / test_secret_redaction 会以 config 的默认 data_dir
为准，也就是仓库里的 ``data/``。加密迁移（P3-04 E）之后那里是锁定的实库：
2026-09-18 实测直接 ``pytest tests/`` 会打到真实库上——轻则用例假红，重则
维护/重置类用例改写真实数据。

本文件在收集任何测试模块之前把变量钉到一次性临时目录，保证「跑回归」与
「碰真实数据」是两件事。需要针对特定目录跑测试时显式传 TZTUZHAN_DATA_DIR。
"""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("TZTUZHAN_DATA_DIR"):
    os.environ["TZTUZHAN_DATA_DIR"] = tempfile.mkdtemp(prefix="tztuzhan_pytest_")

# 睡眠门控测试豁免：她的睡眠时段（深夜）会让所有断言 mock 回复非空的套件
# 整批假红（睡眠门控对前两条消息返回空回复）。suite_runner 复制本进程环境
# 到每个测试子进程，置一次即全套生效；生产环境不设此变量，门控照常工作。
os.environ.setdefault("TZTUZHAN_NO_SLEEP_GATE", "1")
