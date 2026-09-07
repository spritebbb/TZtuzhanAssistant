# M9 P0-01A 普通对话输出卫生交付记录

> 执行：Codex（GPT-5），2026-09-07。
> 基线：`5bcf455`；范围仅 P0-01A，未实施主动消息/日记/草稿等 P0-01B 出口。

## 最终行为

- 新增 `backend/core/output_hygiene.py`，用纯函数识别围栏外的 reasoning 标签、工具协议、特殊角色 token、已知内部指令片段及非用户请求的系统提示泄漏。
- 完整闭合的推理块可确定性删除；删除后无正文、无法可靠清理、重写仍不安全时使用本地安全回退。Markdown 代码围栏中的字面协议示例保持原样。
- `pipeline.py` 在 `output_hygiene_enabled` 开启时完整缓冲普通流式与工具最终答复；插件 `apply_reply` 后检查，通过后的同一 `reply` 才用于 stream、表情判断、解锁摘要、解释快照、临时返回、存档和长期记忆。
- 重复回复与卫生修复共享一次模型重写预算；重写不携带 tools，不会重复执行工具。插件在重写后再次执行，随后再次检查。
- 工具进度仍由 `progress_cb` 实时发送；正文从逐 token 改为检查后按既有 6 字符 chunk 发送。开关关闭保持旧流式行为。

## 文件与数据

- 新建：`backend/core/output_hygiene.py`、`tests/test_output_hygiene.py`。
- 修改：`backend/core/pipeline.py`、`backend/core/features.py`。
- 无 schema、迁移、reset 或导出变化。
- 动态开关 `output_hygiene_enabled` 按全局约定登记在 `FLAG_DEFAULTS`，默认 `False`；当前仓库尚无功能开关 UI，后续设置区接入前可通过既有 `features.set_flag` 写入。未修改真实 `data/feature_flags.json`。

## 验证

- `python -m py_compile`：通过。
- `tests/test_output_hygiene.py`：通过。覆盖跨 chunk 推理、插件重新污染、工具循环最终答复、代码围栏例外、临时轮、单次共享重写预算及屏幕=存档。
- `tests/test_pipeline_scenario.py`：7/7。
- `tests/test_ephemeral_privacy.py`：通过。
- `tests/test_persona_eval.py`：通过。
- 后端完整聚合：`72 passed in 471.25s`。
- `git diff --check`：通过，仅有仓库现有 CRLF 提示。

## 限制与下一步

- P0-01B 仍需把同一出口接到 greeting、initiative、daily、focus、activities 和 M8 草稿/告别信；本提交不声称这些出口已受保护。
- 当前整条缓冲提高首字延迟，这是安全边界的预期代价；后续只能做句子级“先审后发”优化，不能恢复未经检查的 raw token 推送。
- 下一独立切片按路线为 P0-01B 或 P0-02 每日备份加固；不要与本提交混做。
