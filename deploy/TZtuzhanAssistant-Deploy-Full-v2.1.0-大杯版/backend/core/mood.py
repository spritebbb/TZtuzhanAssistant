"""心情系统：菟菚的心情值 0-100，随天气、时间、互动自然变化。

- 心情值 mood：0-100，初始 60。映射为情绪状态（低落/平淡/慵懒/开心/雀跃）。
- 天气影响：每日通过搜索/天气 API 获取当日天气，设置当日心情基线。
- 小时波动：心情随时间缓慢漂移（向基线回归 + 随机扰动），模拟真人情绪起伏。
- 互动影响：用户讲趣事/关心 → 回升；冒犯/冷落/刷屏 → 下降。
- 影响好感度：心情好时互动加分更多，心情差时更容易扣分（由 affection 读取）。

所有可调规则（心情阈值/天气基线/互动增减量/好感度倍率/触发词）集中在
mood_rules 模块，可用 data/mood_rules.json 覆盖，无需改代码。
"""
import random
import re
import threading
from datetime import date, datetime, timedelta

from .log import logger
from .mood_rules import (
    bad_re,
    bonus_multiplier as get_bonus_multiplier,
    compiled_pattern,
    delta_bad,
    delta_care,
    delta_fun,
    delta_good_news,
    idle_params,
    mood_levels as get_mood_levels,
    special_day_bonus,
    weather_base as get_weather_base,
)

# ---- 心情映射（规则来自 mood_rules，可配置）----
MOOD_LEVELS = tuple(tuple(x) for x in get_mood_levels())

# ---- 天气 → 心情基线映射（可配置）----
# 不再在 import 时固化：每次读取都走 mood_rules 的实时加载（带 mtime 缓存），
# 改 data/mood_rules.json 的 weather_base 后无需重启即可生效


def mood_label(mood: int) -> tuple[str, str]:
    """心情值 → (状态名, 描述)。"""
    # 动态读取规则，支持 data/mood_rules.json 热改
    levels = tuple(tuple(x) for x in get_mood_levels())
    name, desc = levels[0][1], levels[0][2]
    for threshold, n, d in levels:
        if mood >= threshold:
            name, desc = n, d
    return name, desc


def weather_baseline(weather: str) -> int:
    """天气关键词 → 心情基线（0-100）。未知天气返回 None（用默认基线）。"""
    for kw, base in get_weather_base().items():
        if kw in weather:
            return base
    return None


# ---- 小时波动：向基线回归 + 随机扰动 ----
def _drift(mood: int, baseline: int, hours_since_update: float) -> int:
    """按经过的小时数让心情向基线回归，并加一点随机扰动。"""
    # 回归力度：每小时向基线靠 8%（越久越靠拢）
    pull = (baseline - mood) * min(1.0, 0.08 * hours_since_update)
    # 随机扰动：每小时 ±3，随时间累积
    noise = random.uniform(-3, 3) * min(1.0, hours_since_update)
    new_mood = mood + pull + noise
    return max(0, min(100, round(new_mood)))


# ---- 互动检测（正则来自 mood_rules，可配置）----
def mood_delta_from_text(text: str) -> int:
    """根据用户这句话判断心情增减（正=回升，负=下降）。返回调整量。"""
    t = text or ""
    delta = 0
    if bad_re().search(t):
        delta += delta_bad()  # 冒犯/辱骂：骤降
    if compiled_pattern("fun").search(t):
        delta += delta_fun()  # 有趣的事：回升
    if compiled_pattern("good_news").search(t):
        delta += delta_good_news()  # 分享好消息：明显回升
    if compiled_pattern("care").search(t):
        delta += delta_care()  # 关心菟菚：回升
    return delta


def idle_decay(hours_idle: float) -> int:
    """被冷落的时间越长，心情越低落（每小时 -0.5，封顶 -15）。"""
    per_hour, cap = idle_params()
    return -min(cap, round(hours_idle * per_hour))


# ---- 天气获取（搜索优先，免费 API 备用）----
_WEATHER_CACHE: dict[str, tuple[date, str, int, datetime]] = {}  # city → (date, weather, baseline, fetched_at)
_WEATHER_LOCK = threading.Lock()


def _weather_cache_get(city: str, today: date) -> tuple[str, int] | None:
    """读缓存；天气为「未知」（上次获取失败）时只信任 30 分钟内，超时重新获取。"""
    cached = _WEATHER_CACHE.get(city)
    if not cached or cached[0] != today:
        return None
    weather, base, fetched = cached[1], cached[2], cached[3]
    if weather != "未知":
        return weather, base
    # 上次失败：30 分钟内不重试（避免频繁请求），超时允许重试
    if (datetime.now() - fetched).total_seconds() < 1800:
        return weather, base
    return None


def _weather_via_search(city: str) -> str | None:
    """用现成搜索查今日天气，返回天气描述（如「晴」）；失败返回 None。"""
    # 天气词分两类处理，避免子串误伤：
    # - 多字词（沙尘/多云）：直接子串匹配，本身语义明确。
    # - 单字词（晴/阴/雨/雪/雷/雾/霾/风）：单字太容易被固定复合词吞掉
    #   （「风」→风格/风云、「晴」→晴朗、「阴」→阴间/阴谋、「雨」→风雨无阻、
    #   「雷」→雷军/雷蛇、「霾」→霾天很少但「霾」本身明确、「雾」→雾化/雾里看花）。
    #   因此单字词只在「有明确天气上下文」时才认：
    #     1) 前后紧跟天气指示字（多云转晴/晴转多云/小雨/大风/有风/雷阵雨/阵雨…）
    #     2) 或整段出现「天气」「温度」「气温」「℃」「风力」等天气语义标记，此时单字大概率是天气。
    _WEATHER_MULTI = ("沙尘", "多云")
    _WEATHER_SINGLE = ("晴", "阴", "雨", "雪", "雷", "雾", "霾", "风")
    # 单字天气词常搭配的前后缀（构成"天气短语"，如 小雨/大风/转晴/有风/雷阵雨）
    _WEATHER_CONTEXT = (
        "转", "有", "小", "中", "大", "阵", "雷阵", "暴", "阴", "晴", "多",
        "微", "轻", "浮", "扬", "尘", "沙", "天", "天气", "气温", "温度", "风力",
        "℃", "级", "度", "下", "刮", "起", "降",
    )
    # 明确的天气语义标记：出现则整段进入"天气语境"，单字可放心认
    _WEATHER_MARKERS = ("天气", "气温", "温度", "风力", "℃", "多云", "降水", "湿度")

    def _has_weather_context(text: str, kw: str) -> bool:
        # 找到该单字的所有出现位置，检查前后是否有天气上下文字
        for m in re.finditer(re.escape(kw), text):
            s, e = m.start(), m.end()
            before = text[max(0, s - 1):s]
            after = text[e:e + 1]
            if before in _WEATHER_CONTEXT or after in _WEATHER_CONTEXT:
                return True
        return False

    try:
        from .search import web_search

        results = web_search(f"{city} 今天 天气", max_results=3)
        for r in results:
            text = (r.get("snippet") or "") + " " + (r.get("title") or "")
            # 多字词：直接子串匹配
            for kw in _WEATHER_MULTI:
                if kw in text:
                    return kw
            # 单字词：需天气上下文或整段天气语境
            in_weather_ctx = any(mk in text for mk in _WEATHER_MARKERS)
            for kw in _WEATHER_SINGLE:
                if kw not in text:
                    continue
                if in_weather_ctx or _has_weather_context(text, kw):
                    return kw
    except Exception:
        pass
    return None


def _weather_via_wttr(city: str) -> str | None:
    """备用：用免费 wttr.in 天气 API 拉当日天气（无需 key）。"""
    try:
        import urllib.parse
        import urllib.request

        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=3&lang=zh"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            line = resp.read().decode("utf-8", "ignore")
        # 形如 "北京: ⛅ 多云, +25°C" 或 "襄阳: 🌦️  +31°C"
        for kw in get_weather_base():
            if kw in line:
                return kw
        # emoji 映射（wttr.in 常用）
        emoji_map = {
            "☀": "晴", "🌞": "晴", "🌤": "晴", "🌣": "晴",
            "⛅": "多云", "🌥": "多云", "☁": "多云", "🌦": "雨",
            "🌧": "雨", "⛈": "雷", "🌨": "雪", "❄": "雪", "🌬": "风",
            "🌫": "雾", "🌪": "风", "☔": "雨",
        }
        for emoji, kw in emoji_map.items():
            if emoji in line:
                return kw
        # 英文关键词兜底
        low = line.lower()
        if "sunny" in low or "clear" in low:
            return "晴"
        if "cloud" in low or "overcast" in low:
            return "多云"
        if "rain" in low or "drizzle" in low or "shower" in low:
            return "雨"
        if "snow" in low or "blizzard" in low:
            return "雪"
        if "thunder" in low or "storm" in low:
            return "雷"
        if "fog" in low or "mist" in low:
            return "雾"
        if "wind" in low:
            return "风"
    except Exception:
        pass
    return None


def today_weather(city: str) -> tuple[str, int]:
    """获取今日天气与心情基线：搜索优先 → wttr.in 备用 → 时间基线兜底。

    返回 (天气描述, 心情基线)。成功结果缓存当天；失败兜底只缓存 30 分钟，
    之后允许重新获取（避免网络抖动一次失败锁死全天天气）。
    """
    today = date.today()
    with _WEATHER_LOCK:
        cached = _weather_cache_get(city, today)
        if cached:
            return cached

    weather = _weather_via_search(city) or _weather_via_wttr(city)
    now = datetime.now()
    if weather:
        base = weather_baseline(weather)
        if base is not None:
            with _WEATHER_LOCK:
                _WEATHER_CACHE[city] = (today, weather, base, now)
            return weather, base

    # 兜底：按时间段/季节给一个温和基线
    hour = now.hour
    if 5 <= hour < 11:
        base = 62
    elif 11 <= hour < 14:
        base = 68
    elif 14 <= hour < 18:
        base = 70
    elif 18 <= hour < 23:
        base = 66
    else:
        base = 55
    with _WEATHER_LOCK:
        _WEATHER_CACHE[city] = (today, "未知", base, now)
    return "未知", base


# ---- 心情状态机：连接天气基线 / 日程情绪 / 小时漂移 / 互动 / 好感度联动 ----
def _baseline_for(city: str, user_id: str = "") -> int:
    """当日心情基线（天气基线；日程/节日加成已随模块移除）。"""
    _, base = today_weather(city)
    return max(0, min(100, base))


def current_mood(user_id: str, *, city: str = "") -> tuple[int, str]:
    """读取用户当前心情（应用小时漂移 + 日程时段切换校正 + 特殊日子加成后），返回 (心情值, 状态名)。

    自动按上次更新时间做漂移回归；若跨了日程时段（如下午→晚上），
    按时段情绪差即时校正心情；今日有特殊日子（生日/纪念日）且上次心情
    更新不在今天时，一次性加上当日加成——让"日程影响心情"立即可感。
    """
    from .userdb import db

    mood, updated = db.get_mood(user_id)
    baseline = _baseline_for(city, user_id) if city else 60

    # 特殊日子当日加成：今日有特殊日子（生日/纪念日）且上次心情更新不是今天 → 一次性加上
    try:
        from .userdb import get_today_important_dates

        special_dates = get_today_important_dates(user_id)
        if special_dates:
            bonus = special_day_bonus()  # 特殊日子当日加成（可配置）
            last_date = None
            if updated:
                try:
                    last_date = datetime.fromisoformat(updated).date()
                except Exception:
                    pass
            if last_date != date.today():
                mood = max(0, min(100, mood + bonus))
                db.set_mood(user_id, mood)
                # 加成已刷新库内时间戳；同步局部 updated，避免 drift 按
                # 昨天的时间差把加成全额拉回基线
                updated = datetime.now().isoformat(timespec="seconds")
    except Exception:
        pass

    if updated:
        try:
            last = datetime.fromisoformat(updated)
            now = datetime.now()
            hours = (now - last).total_seconds() / 3600

            # 日程时段切换校正已移除（日程模块被砍）

            if hours > 0.25:  # 超过 15 分钟才漂移
                new_mood = _drift(mood, baseline, hours)
                # 漂移结果与当前值相同则跳过写库，避免「读心情」高频触发无意义的
                # SQLite 写（on_message 链路里 current_mood 可能被多次调用）。
                if new_mood != mood:
                    mood = new_mood
                    db.set_mood(user_id, mood)
        except Exception:
            pass
    label, _ = mood_label(mood)
    return mood, label


def update_mood(user_id: str, delta: int, *, city: str = "") -> int:
    """按互动结果增减心情值，返回更新后的心情值。"""
    from .userdb import db

    mood, _ = current_mood(user_id, city=city)
    new_mood = max(0, min(100, mood + delta))
    db.set_mood(user_id, new_mood)
    return new_mood


def idle_decay_if_due(user_id: str, *, city: str = "") -> int:
    """仅应用「冷落衰减」：距上次消息超过 12 小时，心情按被冷落的时长下降。

    与 on_user_message 的区别：本函数**只做冷落衰减，不做关键词互动检测**。
    拟人核心层改造后，「这句话对心情的影响」统一由 state.apply_impulse 的
    语义感知负责（感知失败时由 perception 内部用 mood_delta_from_text 兜底），
    这里不再重复检测，避免一句话被新旧两套逻辑各算一次心情。
    """
    from .userdb import db

    try:
        last_ts = db.last_message_ts(user_id)
        if last_ts:
            last = datetime.fromisoformat(last_ts)
            hours_idle = (datetime.now() - last).total_seconds() / 3600
            if hours_idle > 12:
                return update_mood(user_id, idle_decay(hours_idle), city=city)
    except Exception:
        pass
    return current_mood(user_id, city=city)[0]


def on_user_message(user_id: str, text: str, *, city: str = "") -> int:
    """用户每发一条消息时更新心情：应用互动检测 + 自然波动，返回新心情值。

    先按"上次聊天距今多久"算冷落衰减（长时间没聊 → 心情先降），
    再叠加本条消息的互动影响（有趣→升、冒犯→降）。

    注意：拟人核心层改造后，pipeline 主流程已不再调用本函数（改走
    idle_decay_if_due + apply_impulse 语义感知），这里保留作为关键词规则的
    独立入口 / 兼容旧调用方。
    """
    # 冷落衰减：距上次消息超过 12 小时开始掉心情
    from .userdb import db

    try:
        last_ts = db.last_message_ts(user_id)
        if last_ts:
            last = datetime.fromisoformat(last_ts)
            hours_idle = (datetime.now() - last).total_seconds() / 3600
            if hours_idle > 12:
                update_mood(user_id, idle_decay(hours_idle), city=city)
    except Exception:
        pass
    delta = mood_delta_from_text(text)
    return update_mood(user_id, delta, city=city)


def describe(user_id: str, *, city: str = "") -> str:
    """返回心情状态描述（含天气基线说明），供 /心情 命令与调试。"""
    from .userdb import db

    mood, _ = current_mood(user_id, city=city)
    label, desc = mood_label(mood)
    weather = ""
    if city:
        w, base = today_weather(city)
        weather = f"（今日天气：{w}，基线 {base}）"
    bar_amt = mood // 10
    bar = "█" * bar_amt + "░" * (10 - bar_amt)
    return f"心情 {mood} · {label}{weather}\n{bar}\n{desc}"


def mood_bonus_multiplier(mood: int) -> float:
    """心情 → 好感度增减倍率：心情好加分更多，心情差更容易扣分。

    规则来自 mood_rules（默认：雀跃×1.5 / 开心×1.2 / 正常×1.0 / 平淡×0.8 / 低落×0.6）。
    """
    for threshold, mult in sorted(get_bonus_multiplier(), reverse=True):
        if mood >= int(threshold):
            return float(mult)
    return 0.6
