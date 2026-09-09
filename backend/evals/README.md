# Q1 人格与关系评测

默认 smoke 完全离线，以 27 条脱敏 fixture 的参考回复检查确定性规则：

```powershell
python -m backend.evals.runner --output .tmp/q1-smoke.json
```

报告记录固定随机种子、fixture hash、`scorer_version`、配置白名单快照、逐 case hash、失败原因和聚合分数，不记录 API Key。真实模型输出先整理为 `case_id -> CandidateOutput`，自然度使用 1–5 分并附证据，再交给 `run_offline` 和 `release_gate` 比较基线。

A/B 评审使用 `build_blind_review` 生成匿名顺序，映射表单独保存；同一匿名回复的评分差大于 1 时，`flag_review_disagreements` 会列入人工复核。发布门槛会阻断任一原先通过的硬规则回退，以及相对基线超过 5% 的自然度下降。
