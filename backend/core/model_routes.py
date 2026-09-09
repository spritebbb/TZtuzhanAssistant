"""任务到 OpenAI 兼容端点的不可变路由快照。"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace

from .config import config

TASKS = (
    "chat_routine", "chat_deep", "tool", "batch_diary",
    "batch_other", "extract", "judge", "vision",
)


@dataclass(frozen=True)
class Route:
    task: str
    base_url: str
    key_ref: str
    model: str
    timeout_sec: int
    max_tokens: int
    fallback_tasks: tuple[str, ...] = ()


def _legacy(task: str) -> Route:
    main = Route(task, config.llm_base_url, "LLM_API_KEY", config.llm_model,
                 config.llm_timeout, config.llm_max_tokens)
    if task == "chat_deep":
        return replace(main, model=config.llm_model_strong or config.llm_model)
    if task in {"batch_diary", "batch_other", "extract"} and (
        config.llm_perception_model or config.llm_perception_base_url
    ):
        key_ref = "LLM_PERCEPTION_API_KEY"
        if not config.llm_perception_api_key:
            key_ref = "IMAGE_API_KEY" if config.image_api_key else "LLM_API_KEY"
        return Route(
            task,
            config.llm_perception_base_url or config.llm_base_url,
            key_ref,
            config.llm_perception_model or config.llm_model,
            config.llm_perception_timeout,
            config.llm_max_tokens,
        )
    if task == "extract":
        return replace(_legacy("batch_other"), task="extract")
    if task == "vision":
        base = config.vision_base_url or config.image_base_url or config.llm_base_url
        if config.vision_api_key:
            key_ref = "VISION_API_KEY"
        elif config.image_api_key:
            key_ref = "IMAGE_API_KEY"
        else:
            key_ref = "LLM_API_KEY"
        return Route(task, base, key_ref, config.vision_model or "Qwen/Qwen3-VL-32B-Instruct",
                     60, 1000)
    return main


def _configured(task: str) -> Route:
    legacy = _legacy(task)
    prefix = f"MODEL_ROUTE_{task.upper()}_"
    fallback = tuple(
        part.strip() for part in os.getenv(prefix + "FALLBACK_TASKS", "").split(",") if part.strip()
    )
    def positive_int(name: str, default: int) -> int:
        try:
            return max(1, int(os.getenv(name, "") or default))
        except ValueError:
            return default

    route = Route(
        task=task,
        base_url=os.getenv(prefix + "BASE_URL", "").strip() or legacy.base_url,
        key_ref=os.getenv(prefix + "KEY_REF", "").strip() or legacy.key_ref,
        model=os.getenv(prefix + "MODEL", "").strip() or legacy.model,
        timeout_sec=positive_int(prefix + "TIMEOUT_SEC", legacy.timeout_sec),
        max_tokens=positive_int(prefix + "MAX_TOKENS", legacy.max_tokens),
        fallback_tasks=fallback,
    )
    return route


def _validate_chain(route: Route) -> None:
    if len(route.fallback_tasks) > 1:
        raise ValueError(f"{route.task}: fallback 最多一级")
    if not route.fallback_tasks:
        return
    target = route.fallback_tasks[0]
    if target not in TASKS:
        raise ValueError(f"{route.task}: 未知 fallback task: {target}")
    if target == route.task:
        raise ValueError(f"{route.task}: fallback 配置存在循环")
    target_route = _configured(target)
    if target_route.fallback_tasks:
        if route.task in target_route.fallback_tasks:
            raise ValueError(f"{route.task}: fallback 配置存在循环")
        raise ValueError(f"{route.task}: fallback 超过一级")


def resolve_route(task: str, explicit_model: str | None = None) -> Route:
    """在请求开始时解析路由快照；显式 model 只覆盖模型，不换端点。"""
    if task not in TASKS:
        raise ValueError(f"未知模型任务：{task}")
    route = _configured(task)
    _validate_chain(route)
    return replace(route, model=explicit_model) if explicit_model else route


def fallback_route(route: Route) -> Route | None:
    if not route.fallback_tasks:
        return None
    return replace(resolve_route(route.fallback_tasks[0]), fallback_tasks=())


def resolve_api_key(route: Route) -> str:
    """解析 key_ref；引用名可以是旧配置字段对应环境变量，不返回到报告。"""
    values = {
        "LLM_API_KEY": config.llm_api_key,
        "LLM_PERCEPTION_API_KEY": config.llm_perception_api_key,
        "IMAGE_API_KEY": config.image_api_key,
        "VISION_API_KEY": config.vision_api_key,
    }
    return values.get(route.key_ref, os.getenv(route.key_ref, "").strip())
