# 修复报告：菟菚「不能正常回复」根因诊断与修复

日期：2026-09-03
问题：用户发送消息后，菟菚不能正常回复内容

---

## 一、根因

**LLM API 连接失败（APIConnectionError），由本机失效的临时代理导致。**

### 现象链

1. 后端进程由 `start.bat` 启动时，继承了系统环境变量里的本地代理：
   ```
   HTTP_PROXY=http://127.0.0.1:57622
   HTTPS_PROXY=http://127.0.0.1:57622
   ```
2. 这个代理端口是 WorkBuddy/CodeBuddy 会话的**临时代理**，会随会话重启**漂移/失效**。
3. openai SDK 底层用 httpx，默认 `trust_env=True`，会**读系统代理环境变量**。
4. 代理端口失效后，所有直调 DeepSeek 的请求报：
   ```
   openai.APIConnectionError: Connection error.
   ```
5. **为什么天气能回、聊天不能回**：
   - 天气查询走 `wttr.in`（本地 urllib 搜索）+ 工具循环，**不依赖 LLM 直调** → 能回复
   - 纯聊天需要直调 DeepSeek → 走代理失败 → 报错
   - 造成「时而能回、时而不回」的迷惑现象

### 日志证据

```
backend_stderr.log:
[LLM] 第1次失败（APIConnectionError），1.5s 后重试
[LLM] 第2次失败（APIConnectionError），3.0s 后重试
openai.APIConnectionError: Connection error.
```

---

## 二、修复内容

### 1. `backend/core/llm.py` — 新增代理容错

新增 `_build_http_client()`：
- 未设 `LLM_PROXY` → 返回 None（走 SDK 默认行为）
- `LLM_PROXY=off/direct/none` → 强制直连（`trust_env=False`）
- `LLM_PROXY=<具体地址>` → 走指定代理

`get_client()` / `get_perception_client()` 均接入该 client。

### 2. `backend/core/config.py` — 新增配置项

```python
self.llm_proxy: str = os.getenv("LLM_PROXY", "").strip()
```

### 3. `.env` — 强制直连

```env
LLM_PROXY=off
```

（已验证：本机直连 `api.deepseek.com` 可用，无需代理）

---

## 三、附带清理：脏数据

诊断过程中发现 `sessions.db` / `bot.db` 混入了测试残留数据：

| 类型 | 来源 | 影响 |
|------|------|------|
| `触发异常` 乱码消息 | 测试脚本 `test_http_endpoints.py` 用 `content="text=触发异常"` 未百分号编码，且 `TestClient(app)` 直连生产库 | 污染会话上下文 |
| `RuntimeError: LLM 炸了` 回复 | 同上（测试故意触发异常） | 同上 |
| `你好测试` 乱码 | 诊断时用 curl `--data-binary` 未百分号编码 | 同上 |

已全部清理（删除乱码消息 + RuntimeError 回复），保留真实天气问答记录。备份位于：
- `data/sessions.db.bak_20260903_012858`
- `data/bot.db.bak_20260903_012858`

---

## 四、验证结果

重启后端后，端到端测试：

| 测试 | 结果 |
|------|------|
| 纯聊天「在吗，说句话」 | ✅ 正常回复（带上下文记忆） |
| 天气查询「帮我查一下今天襄阳的天气」 | ✅ 正常回复 |
| `py_compile` 编译检查 | ✅ 通过 |

---

## 五、遗留问题（待后续处理，非本次范围）

1. **测试隔离缺陷**：`test_http_endpoints.py` 用 `TestClient(app)` 直连生产数据库 `data/sessions.db`，且 `content="text=触发异常"` 未做百分号编码。每次跑测试都会往生产库写乱码数据。建议后续改为临时库 + 正确编码。
2. **curl 中文测试规范**：测中文接口必须用 `--data-urlencode`（百分号编码），`--data-binary` 传原始 UTF-8 字节会被 python-multipart 按 latin-1 误读成乱码。
