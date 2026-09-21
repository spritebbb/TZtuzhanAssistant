# GPT-SoVITS 本地语音 Runbook（P3-03）

> 对应技术指导 §21.2。本仓库只交付 **adapter 与流程**（能力协商、显式回退、
> 声纹档案、preflight）；GPT-SoVITS 本体部署、音色素材与训练在仓库外完成。
> 纪律：不把任何教程参数当稳定合同——一切以运行时 `/capabilities` 协商为准。

## 0. 总览

```
素材（你有权使用的 5-10 分钟干净音频）
   └─ scripts/voice/preflight.py 检查
        └─ GPT-SoVITS 仓库内训练（外部，记录 commit）
             └─ 推理服务（api_v2 风格 + 本 runbook 的 /capabilities 薄包装）
                  └─ 菟菚后端 adapter：协商 → 声纹档案 → 合成 → 失败回退 edge-tts
```

## 1. 部署 GPT-SoVITS（仓库外）

1. 单独目录克隆 GPT-SoVITS（建议独立 venv，不与本项目 `.venv` 混装 CUDA 依赖）。
2. 记录当前 **commit hash**——它是 `voice_profiles.provider_version` 的值，
   升级 commit 后必须重新协商并登记新档案（缓存键含版本，自动失效）。
3. 启动其 api 推理服务（默认端口 9880）。

## 2. /capabilities 薄包装（必须）

在推理服务前加一层（或直接在 api 脚本里加一个 GET 路由），返回：

```json
{
  "provider_version": "<commit hash 前 12 位>",
  "model_format": "gpt-sovits-v2",
  "sample_rate": 32000,
  "languages": ["zh", "en"],
  "gpu": true,
  "cpu_inference_ok": false
}
```

字段语义：`gpu=false` 且 `cpu_inference_ok=false` → adapter 仍会尝试，但延迟
不达标时应关 flag；**字段缺失 `provider_version` 视为协商失败，直接回退**。

## 3. 素材准备（训练前）

- 5–10 分钟**音色一致、干净**（去静音/爆音）的音频，切成 2–15 秒的句段；
- 每段配同名 `.txt` 标注文本（GPT-SoVITS 的 prompt_text 依赖它）；
- 目录放 `SOURCE.md` 写清来源与使用权——**素材必须有权使用**；
- 目录建议：`data/voice_materials/<persona>/`（加入备份排除；训练中间产物
  放加密工作区，任务完成一键清理）；
- 质量门槛是**试听盲评**（10 轮），分钟数不是保证。

## 4. Preflight 与训练

```bash
.venv/Scripts/python.exe scripts/voice/preflight.py --materials data/voice_materials/default
```

FAIL 清零后，在 GPT-SoVITS 仓库内按其官方流程训练；训练/验证集分离。
**本项目不代跑训练**（批次15 口径：不训练）。

## 5. 登记声纹并启用

```python
# 一次性登记（示例；后续可做成设置页按钮）
from backend.core.local_tts import add_voice_manifest, set_voice_profile
m = add_voice_manifest(path="voice/gpt-sovits/ref-default.wav",
                       text="（参考音频的标注文本）", lang="zh",
                       sha256="<文件sha256>", duration_sec=8.2)
set_voice_profile("default", provider_version="<commit>",
                  model_hash="<模型文件sha256前16位>",
                  reference_manifest_id=m["manifest_id"])
```

然后在设置页打开「本地语音」（`local_tts_enabled`），或 `.env`：

```
LOCAL_TTS_ENDPOINT=http://127.0.0.1:9880
LOCAL_TTS_TIMEOUT=60
```

## 6. 行为契约（adapter 已实现，验收对照）

- 朗读请求先协商（60s 缓存）→ 有启用声纹才走本地；任一步失败**显式回退
  edge-tts**，响应头 `X-TTS-Provider` 标明实际使用方；
- `GET /api/tts/provider` 诊断协商与档案状态；
- 分句不切代码块与 URL；长文本 ≤5 块合成后拼接 PCM（格式不一致即回退）；
- GPU 单飞：同声纹请求串行，保持消息顺序；
- 缓存键 = provider+版本+模型+声纹+韵律+文本，与 edge-tts 缓存隔离；
- 验收清单（§21.2）：同一句多情绪、中/英、长句、取消、切人格、缓存失效、
  10 轮盲听；**不做「像真人」的医疗或身份宣称**。

## 7. 降级与关闭

- 服务挂/显存不足/版本不符 → 自动回退，日志 `[本地语音] ... 回退 edge-tts`；
- 想彻底关：设置页关「本地语音」即可（零代码改动回到现状）。
