"""配置加载：从项目根目录 .env 读取，提供全局 Config 对象。"""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # backend/core/config.py → 项目根
load_dotenv(PROJECT_ROOT / ".env")


def _env_float(name: str, default: float) -> float:
    """读环境变量为 float；缺失/非数字/空串一律用默认值（保证启动不崩）。"""
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    """读环境变量为 int；缺失/非数字/空串一律用默认值。"""
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


class Config:
    def __init__(self) -> None:
        self._read()

    def _read(self) -> None:
        """从进程环境变量（.env 已 load）读取全部配置。供 reload 复用。"""
        self.llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
        self.llm_api_key: str = os.getenv("LLM_API_KEY", "")
        self.llm_model: str = os.getenv("LLM_MODEL", "deepseek-chat")
        # 强模型路由（D5）：写作/代码/长文类请求可走更强的模型。留空=全部走主模型。
        self.llm_model_strong: str = os.getenv("LLM_MODEL_STRONG", "").strip()
        # 成本面板价格（元 / 百万 tokens），DeepSeek-chat 参考价：入 1 / 出 2
        self.llm_price_input_per_mtok: float = _env_float("LLM_PRICE_INPUT_PER_MTOK", 1.0)
        self.llm_price_output_per_mtok: float = _env_float("LLM_PRICE_OUTPUT_PER_MTOK", 2.0)
        self.llm_temperature: float = _env_float("LLM_TEMPERATURE", 0.8)
        self.llm_max_tokens: int = _env_int("LLM_MAX_TOKENS", 500)
        # 流式逃生开关：某些端点不支持 stream 时退回整句返回（1=关流式，0=开）
        self.llm_stream_disable: bool = os.getenv("LLM_STREAM_DISABLE", "0") != "0"
        # 主 LLM 单次请求超时（秒）。网络不稳时调小，让失败更快暴露、更快重试；
        # 默认 45。max_tokens 配得很大时需适当调大，否则会误杀正常长生成。
        self.llm_timeout: int = _env_int("LLM_TIMEOUT", 45)

        # LLM 请求代理。默认空（openai SDK 读系统代理环境变量）。
        # 本机若存在随会话漂移的临时代理（如 127.0.0.1:xxxxx），会导致外网请求
        # 间歇性全挂；此时设 LLM_PROXY=off 强制直连（trust_env=False）即可绕过。
        self.llm_proxy: str = os.getenv("LLM_PROXY", "").strip()

        # 感知层独立小模型：语义感知（perception）是高频轻量调用（每条消息一次），
        # 用它专属的模型/端点可显著降低延迟与成本。留空则复用主 LLM。
        self.llm_perception_model: str = os.getenv("LLM_PERCEPTION_MODEL", "").strip()
        self.llm_perception_base_url: str = os.getenv("LLM_PERCEPTION_BASE_URL", "").strip()
        self.llm_perception_api_key: str = os.getenv("LLM_PERCEPTION_API_KEY", "").strip()
        # 感知层超时（秒）。感知已后台化不阻塞回复，且硅基流动 Qwen3-8B 单条实测
        # 10-35s，超时过小(30s)会频繁触发 APITimeoutError 重试、刷"LLM 连接失败"
        # 假警报。放宽到 60s 覆盖正常慢响应（后台跑，无碍回复首字）。
        self.llm_perception_timeout: int = _env_int("LLM_PERCEPTION_TIMEOUT", 60)

        persona = os.getenv("PERSONA_FILE", "persona-菟菚.md")
        p = Path(persona)
        self.persona_file: Path = p if p.is_absolute() else (PROJECT_ROOT / p)

        data_dir = os.getenv("TZTUZHAN_DATA_DIR", "").strip()
        data_path = Path(data_dir) if data_dir else (PROJECT_ROOT / "data")
        self.data_dir: Path = data_path if data_path.is_absolute() else (PROJECT_ROOT / data_path)
        self.search_enabled: bool = os.getenv("SEARCH_ENABLED", "1") != "0"
        self.search_engine: str = os.getenv("SEARCH_ENGINE", "bing").lower()
        self.search_api_key: str = os.getenv("SEARCH_API_KEY", "").strip()

        # 记忆语义检索：用户疑似回忆（上次/之前/还记得…）时，先用 LLM 把问题
        # 扩展成多个检索词再查长期记忆，提升召回；关闭则退回 v1 关键词检索
        self.memory_semantic: bool = os.getenv("MEMORY_SEMANTIC", "1") != "0"

        # 记忆重构（v2）：本地 embedding 模型（BGE-M3 默认，可换 bge-small-zh-v1.5 等）
        self.memory_embed_model: str = os.getenv("MEMORY_EMBED_MODEL", "BAAI/bge-m3").strip()
        # 强制跳过本地模型、只用哈希回退向量（调试用）
        self.memory_embed_force: bool = os.getenv("MEMORY_EMBED_FORCE", "0") != "0"
        # HuggingFace 下载镜像：国内直连 huggingface.co 常超时（WinError 10060），
        # 默认走 hf-mirror.com 镜像下载 embedding 模型；有代理/海外环境可设
        # HF_ENDPOINT=https://huggingface.co 改回官方源。
        self.hf_endpoint: str = os.getenv("HF_ENDPOINT", "https://hf-mirror.com").strip() or "https://huggingface.co"
        # 尽早写入进程环境变量：sentence-transformers / huggingface_hub 在 import 时
        # 读取一次 HF_ENDPOINT，必须保证在它被导入前就位（config 是最早被导入的模块）。
        os.environ["HF_ENDPOINT"] = self.hf_endpoint
        # 记忆引擎总开关：关闭时完全退回旧版（TF-IDF + sqlite-vec）行为
        self.memory_v2: bool = os.getenv("MEMORY_V2", "1") != "0"
        # Mem0 记忆管理器开关
        self.memory_mem0: bool = os.getenv("MEMORY_MEM0", "1") != "0"

        # 图像生成（SiliconFlow 文生图；不配置则生图功能关闭）
        self.image_base_url: str = os.getenv("IMAGE_BASE_URL", "https://api.siliconflow.cn/v1").strip()
        self.image_api_key: str = os.getenv("IMAGE_API_KEY", "").strip()
        self.image_model: str = os.getenv("IMAGE_MODEL", "Kwai-Kolors/Kolors").strip()

        # 心情系统：天气城市（留空则按时间段兜底基线，不查天气）
        self.mood_city: str = os.getenv("MOOD_CITY", "").strip()

        # 主动消息：问候与 initiative 共用每日额度；全部运行时读取，热重载即生效。
        self.proactive_greeting_idle_hours: int = max(1, _env_int("PROACTIVE_GREETING_IDLE_HOURS", 8))
        self.proactive_idle_hours: int = max(1, _env_int("PROACTIVE_IDLE_HOURS", 6))
        self.proactive_daily_max: int = max(1, _env_int("PROACTIVE_DAILY_MAX", 1))
        self.proactive_global_cooldown_sec: int = max(30, _env_int("PROACTIVE_GLOBAL_COOLDOWN_SEC", 900))
        self.proactive_check_interval_sec: int = max(30, _env_int("PROACTIVE_CHECK_INTERVAL_SEC", 300))
        self.proactive_failure_cooldown_sec: int = max(30, _env_int("PROACTIVE_FAILURE_COOLDOWN_SEC", 900))
        self.proactive_image_enabled: bool = os.getenv("PROACTIVE_IMAGE_ENABLED", "1") != "0"
        self.proactive_image_chance_percent: int = max(0, min(100, _env_int("PROACTIVE_IMAGE_CHANCE_PERCENT", 20)))
        self.proactive_image_min_mood: int = max(0, min(100, _env_int("PROACTIVE_IMAGE_MIN_MOOD", 70)))

        # 自制表情包：仅在有明确情绪场景时低频附带，优先复用收藏。
        self.sticker_enabled: bool = os.getenv("STICKER_ENABLED", "1") != "0"
        self.sticker_chance_percent: int = max(0, min(100, _env_int("STICKER_CHANCE_PERCENT", 10)))
        self.sticker_min_message_gap: int = max(2, _env_int("STICKER_MIN_MESSAGE_GAP", 10))
        self.sticker_collection_max: int = max(4, _env_int("STICKER_COLLECTION_MAX", 24))

        # 知识库（D2 RAG）：用户投喂 pdf/txt/md，语义检索相关段落注入对话。
        # 本地检索（BGE-M3）无 LLM 成本；距离阈值门控，不像就不注入，避免硬凑。
        self.kb_enabled: bool = os.getenv("KB_ENABLED", "1") != "0"
        self.kb_chunk_size: int = max(200, _env_int("KB_CHUNK_SIZE", 600))
        self.kb_chunk_overlap: int = max(0, _env_int("KB_CHUNK_OVERLAP", 120))
        self.kb_recall_top_k: int = max(1, min(10, _env_int("KB_RECALL_TOP_K", 3)))
        # cosine 距离阈值：小于该值才认为相关（bge-m3 上 0.55 约等于「有点关系」）
        self.kb_recall_max_distance: float = _env_float("KB_RECALL_MAX_DISTANCE", 0.55)
        # 单文件大小上限（MB）与单用户文档数上限
        self.kb_max_file_mb: int = max(1, _env_int("KB_MAX_FILE_MB", 20))
        self.kb_max_documents: int = max(1, _env_int("KB_MAX_DOCUMENTS", 50))

        # 图片理解（视觉模型：SiliconFlow / DashScope 等 OpenAI 兼容视觉端点）
        self.vision_base_url: str = os.getenv("VISION_BASE_URL", "").strip()
        self.vision_api_key: str = os.getenv("VISION_API_KEY", "").strip()
        self.vision_model: str = os.getenv("VISION_MODEL", "").strip()

        # ---- Agent 能力配置组 ----
        # 每步确认：写/命令/外部类工具执行前是否弹确认（1=开，0=关）
        self.agent_confirm_enabled: bool = os.getenv("AGENT_CONFIRM_ENABLED", "1") != "0"
        # 确认超时（秒）：用户不响应则自动按拒绝处理
        self.agent_confirm_timeout: int = _env_int("AGENT_CONFIRM_TIMEOUT", 60)
        # 无 SSE 通道时的确认策略：deny=写/命令/外部工具默认拒绝（安全默认）；
        # allow=放行（旧版本地信任行为，谨慎使用）
        self.agent_confirm_no_channel: str = os.getenv("AGENT_CONFIRM_NO_CHANNEL", "deny").strip().lower()
        # 允许读写操作的根目录白名单（分号分隔；文件/命令工具只允许在此范围内操作）
        self.agent_allowed_roots: list[str] = [
            p.strip() for p in os.getenv("AGENT_ALLOWED_ROOTS", "").split(";") if p.strip()
        ]
        # 危险命令黑名单（语义级；命中直接拒绝，不弹确认）
        # 注意：条目按子串匹配，避免裸 "format" 之类误伤合法命令（如 git log --format=）
        self.agent_block_cmds: list[str] = [
            p.strip().lower() for p in os.getenv(
                "AGENT_BLOCK_CMDS",
                "rd /s;rm -rf;del /f;shutdown;reg delete;diskpart;net user;takeown;icacls;taskkill /f /im;powershell -enc;format c:;format /q;Remove-Item -Recurse;Clear-Content",
            ).split(";") if p.strip()
        ]
        # 工具结果截断上限（字符；超长输出压缩头尾）
        self.agent_max_output_chars: int = _env_int("AGENT_MAX_OUTPUT_CHARS", 4000)
        # 外部 Agent 桥：Codex CLI 路径（留空则自动探测）
        self.agent_codex_path: str = os.getenv("AGENT_CODEX_PATH", "").strip()
        # 外部 Agent 桥：Codex profile 名（~/.codex/<name>.config.toml）
        self.agent_codex_profile: str = os.getenv("AGENT_CODEX_PROFILE", "deepseek").strip()
        # 外部 Agent 桥：Codex 工作目录（默认项目根）
        self.agent_codex_cwd: str = os.getenv("AGENT_CODEX_CWD", "").strip() or str(PROJECT_ROOT)
        # 外部 Agent 桥：Codex 单次任务超时（秒）
        self.agent_codex_timeout: int = _env_int("AGENT_CODEX_TIMEOUT", 180)
        # 外部 Agent 桥：DSH CLI 路径（留空则自动探测 dsh 命令）
        self.agent_dsh_cli: str = os.getenv("AGENT_DSH_CLI", "").strip()
        # 外部 Agent 桥：DSH headless profile 名
        self.agent_dsh_profile: str = os.getenv("AGENT_DSH_PROFILE", "headless").strip()
        # 外部 Agent 桥：DSH 单次任务超时（秒，默认 120；与 Codex 的
        # agent_codex_timeout 对齐，可配置化避免长任务被硬编码超时误杀）
        self.agent_dsh_timeout: int = _env_int("AGENT_DSH_TIMEOUT", 120)
        # MCP 标准通道 / 远程任务鉴权 token（空 = 仅本机）
        self.agent_remote_token: str = os.getenv("AGENT_REMOTE_TOKEN", "").strip()
        # 远程任务鉴权（来源 IP 语义）：
        # - 回环来源始终免 token（本机进程信任，与绑定地址无关）；
        # - 非回环来源必须配置并携带 AGENT_REMOTE_TOKEN。
        # agent_remote_allow_empty 为历史开关，来源 IP 语义下不再影响判定，
        # 仅保留兼容读取（默认 1）。
        self.agent_remote_allow_empty: bool = os.getenv("AGENT_REMOTE_ALLOW_EMPTY_TOKEN", "1") != "0"
        # Host 白名单（分号分隔）：后端只响应这些 Host，防 DNS rebinding/外部直连。
        # 默认本机；--host 0.0.0.0 局域网访问时追加你的局域网地址，如 192.168.1.10:8801。
        # 注意：Host 白名单决定"谁能到达"，来源 IP 语义决定"是否免 token"，
        # 两者独立（局域网 Host 放行后仍需要 token）。
        self.agent_allowed_hosts: list[str] = [
            p.strip().lower() for p in os.getenv(
                "AGENT_ALLOWED_HOSTS",
                "127.0.0.1:8801;localhost:8801;[::1]:8801",
            ).split(";") if p.strip()
        ]

    def reload(self) -> None:
        """热重载：重新加载 .env（覆盖已读入的环境变量）并刷新属性。

        调用方还需自行重置依赖缓存的模块状态（如 llm._client、
        persona._persona_cache），本类不做跨模块 import（避免循环依赖）。
        """
        load_dotenv(PROJECT_ROOT / ".env", override=True)
        self._read()


config = Config()


def update_env_file(updates: dict[str, str]) -> list[str]:
    """按 key 更新 .env 文件（保留注释与顺序），返回实际更新的 key 列表。

    - 只更新「未注释」的 KEY=value 行；注释示例行保持不动。
    - key 不存在则追加到文件末尾。
    - 调用方随后应 config.reload() 让新值生效。
    """
    env_path = PROJECT_ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True) if env_path.exists() else []
    updated: list[str] = []
    for key, value in updates.items():
        key = key.strip().upper()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = str(value).strip()
        # 过滤换行/控制字符，防止通过配置值注入额外的 KEY=value 行
        value = re.sub(r"[\r\n\x00-\x1f\x7f]", "", value).strip()
        # dotenv 语义：值内含 # 会被当注释截断、含空格/引号也需包裹；
        # 对含这些字符的值用双引号包裹并转义内部引号，保证密钥/路径不被静默截断。
        if any(ch in value for ch in ("#", " ", "\t", '"', "'")):
            value = '"' + value.replace('"', '\\"') + '"'
        # 跳过脱敏占位符（前端没改就不动）
        if value.startswith("****") or value.endswith("****") or "****" in value:
            continue
        placed = False
        for i, line in enumerate(lines):
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if m and m.group(1).upper() == key and not line.lstrip().startswith("#"):
                lines[i] = f"{key}={value}\n"
                placed = True
                break
        if not placed:
            lines.append(f"{key}={value}\n")
        updated.append(key)
    env_path.write_text("".join(lines), encoding="utf-8")
    return updated
