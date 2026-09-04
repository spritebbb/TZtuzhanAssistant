# 菟菚远程访问与公网部署方案

> 评估日期：2026-09-04。菟菚包含私密会话、配置和本机 Agent 能力，默认只监听 `127.0.0.1` 是有意的安全边界。

## 结论

按使用范围选择，不建议直接把 `8801` 端口暴露到公网：

| 场景 | 推荐方式 | 暴露范围 | 适合程度 |
|---|---|---|---|
| 只给自己的电脑/手机使用 | **Tailscale Serve** | 仅 tailnet 内已授权设备 | 首选，改动最少 |
| 临时给别人看演示 | Tailscale Funnel | 拿到 URL 的公网用户 | 只用于短时、低敏演示 |
| 固定域名给少数指定用户 | **Cloudflare Tunnel + Access** | 登录且命中 Access Allow 策略的用户 | 推荐的公网共享方案 |
| 需要 24×7 稳定在线 | 云服务器 + Caddy + 进程守护 | 自定义域名 | 运维成本最高 |

## 方案一：Tailscale Serve（个人跨设备首选）

后端继续只监听回环地址：

```powershell
python -m backend.main --host 127.0.0.1 --port 8801
tailscale serve --bg 8801
```

Serve 把本地端口代理到 tailnet 内的 HTTPS 地址，并继承 tailnet 的访问控制；官方还建议在依赖其身份头时让源站只监听 localhost。[Tailscale Serve 官方文档](https://tailscale.com/docs/features/tailscale-serve)

这条路线无需设置 `AGENT_REMOTE_TOKEN`，因为 Tailscale 与应用通常运行在同一台机器，转发到后端时来源为回环；访问权由 tailnet 控制。仍应只邀请可信设备/用户。

## 方案二：Cloudflare Tunnel + Access（固定公网域名）

1. 后端仍监听 `127.0.0.1:8801`。
2. 在 Cloudflare Zero Trust 中先创建 Self-hosted 应用及 Allow 策略，再创建 Tunnel；官方明确提醒，未先配置 Access 时，发布的应用会对互联网公开。[Cloudflare Access 自托管应用](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)
3. 将公开主机名映射到 `http://localhost:8801`。[Cloudflare Tunnel 设置](https://developers.cloudflare.com/tunnel/setup/)
4. 把公开域名（含端口时带端口）加入 `AGENT_ALLOWED_HOSTS`，例如：

```dotenv
AGENT_ALLOWED_HOSTS=127.0.0.1:8801;localhost:8801;tuzhan.example.com
```

Cloudflare Access 负责浏览器登录，但后端看到的转发来源可能不是回环。因此还需设置强随机 `AGENT_REMOTE_TOKEN`，首次访问使用一次 `https://tuzhan.example.com/?token=...`；前端会把 token 保存到当前标签页的 `sessionStorage` 并从地址栏移除，后续 API 与 SSE 自动携带。

不要在聊天、截图或公开文档中传播 token；泄漏后立即轮换。

## 方案三：Tailscale Funnel（仅临时演示）

```powershell
tailscale funnel 8801
```

Funnel 会提供公网 HTTPS URL，任何拿到 URL 的人都能访问；官方也明确建议不要用它暴露敏感服务。[Tailscale Funnel 官方说明](https://tailscale.com/docs/use-cases/application-testing/share-local-dev-server-with-internet)

若必须使用，同样配置 `AGENT_ALLOWED_HOSTS` 与强随机 `AGENT_REMOTE_TOKEN`，演示结束后执行：

```powershell
tailscale funnel reset
```

## 方案四：云服务器 + Caddy（长期在线）

把后端作为普通用户进程运行在 `127.0.0.1:8801`，再由 Caddy 反向代理：

```caddyfile
tuzhan.example.com {
    reverse_proxy 127.0.0.1:8801
}
```

Caddy 在域名 DNS 指向服务器且 80/443 可达时会自动申请、续期证书并把 HTTP 重定向到 HTTPS。[Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https) · [Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)

生产部署还必须做到：

- 只让 Caddy 暴露 80/443，防火墙不开放 8801；
- 用 systemd/NSSM 等守护后端，并为 `data/` 做加密备份；
- 设置 `AGENT_ALLOWED_HOSTS=tuzhan.example.com` 与强随机 `AGENT_REMOTE_TOKEN`；
- 再加一层身份认证（Cloudflare Access、反向代理 Basic/OIDC 等），不能只依赖“URL 不公开”；
- Agent 命令、文件写入、配置修改继续保留逐步确认，不为远程部署放宽安全策略。

## 上线前验收

- 未带 token 的远端请求访问 `/api/meta` 得到 403；带正确 token 才能成功。
- `/api/health` 可用于健康探针，但不返回会话或密钥。
- 浏览器聊天、SSE、主动消息图片、TTS 都通过 HTTPS 同源访问。
- Host 白名单只包含实际域名，不使用 `*`。
- 停止隧道或反向代理后，公网无法再访问源站的 8801 端口。

