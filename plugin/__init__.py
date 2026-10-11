"""spatial-injector v1.0 — Location + channel context injection via pre_llm_call.

Per docs/DESIGN.md v1.2 in this repository:

- Location: reads whereabouts-mcp state file (locations.json, absolute path,
  NEVER home-relative — see v1.2 NSSM/HOME-split fix). Injects a compact
  one-line context: place tag, dwell time, report freshness, battery.
- Channel: pre_llm_call payload natively carries ``platform`` (proven at
  agent/turn_context.py L1383-1391). link.db lookup is only a fallback when
  the platform value cannot distinguish HermesPilot from desktop TUI.
- Privacy: coordinates truncated to 3 decimals in the injected string
  (~110 m). Raw coordinates never enter the conversation record.
- Degradation: any failure (missing file, stale data >24 h, corrupt JSON,
  exception) degrades to time/channel-only — NEVER blocks the conversation.

Injection is ephemeral (API-call time only, not persisted to session DB)
and targets the user message, preserving the prompt-cache prefix — same
mechanism as time-injector.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from hermes_cli.plugins import PluginContext  # noqa: F401

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

# Absolute path (v1.2: never home-relative). Override via env for tests.
STATE_FILE = os.environ.get(
    "SPATIAL_STATE_FILE",
    os.path.join(os.path.expanduser("~"), ".spatial-awareness", "locations.json"),
)

# whereabouts-mcp default layout keeps everything under state_dir/locations.json
FALLBACK_STATE_FILE = os.path.join(
    os.path.expanduser("~"), ".whereabouts-mcp", "locations.json"
)

STALE_SECONDS = 24 * 3600          # location older than this -> "unknown"
COORD_DECIMALS = 3                 # ~110 m privacy truncation
CHANNEL_CACHE_TTL = 300            # seconds; channel lookup cache per process

_channel_cache: dict[str, tuple[float, str]] = {}  # session_id -> (ts, channel)


# ── Location reading ─────────────────────────────────────────────────────────

def _load_locations() -> Optional[dict]:
    """Read the whereabouts state file. Returns dict or None on any failure."""
    for path in (STATE_FILE, FALLBACK_STATE_FILE):
        try:
            if not path or not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _fmt_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{max(minutes, 1)}min"
    return f"{minutes // 60}h{minutes % 60:02d}m"


def _location_piece(data: dict, now_ts: float) -> Optional[str]:
    """Compose the Location: piece. None -> omit (degradation path)."""
    stay = data.get("currentStay") or {}
    if not isinstance(stay, dict):
        return None

    # Freshness: whereabouts stores ISO strings for enter/last-seen.
    last_seen_iso = stay.get("lastSeenAt") or stay.get("lastSeenAtLocal") or ""
    report_age_min: Optional[int] = None
    if last_seen_iso:
        try:
            ls = datetime.fromisoformat(str(last_seen_iso).replace("Z", "+00:00"))
            report_age_min = int(max(0.0, now_ts - ls.timestamp()) // 60)
        except Exception:
            report_age_min = None

    if report_age_min is not None and report_age_min > STALE_SECONDS // 60:
        return f"Location: unknown(上次上报 {report_age_min // 60} 小时前)"

    # Place tag or truncated coordinates.
    place = stay.get("placeTag")
    if place:
        where = str(place)
    else:
        lat, lng = stay.get("centerLat"), stay.get("centerLng") or stay.get("centerLng".replace("Lng", "Lng"))
        if lat is None or lng is None:
            where = "unknown"
        else:
            where = f"{float(lat):.{COORD_DECIMALS}f},{float(lng):.{COORD_DECIMALS}f}f".rstrip("f")

    parts = [f"Location: {where}"]

    # Dwell time: state file does NOT store durationMinutes (computed at MCP
    # output time only) — derive it from enteredAt.
    entered_iso = stay.get("enteredAt")
    if entered_iso:
        try:
            ent = datetime.fromisoformat(str(entered_iso).replace("Z", "+00:00"))
            dwell_s = max(0.0, now_ts - ent.timestamp())
            if dwell_s > 60:
                parts.append(f"已停留{_fmt_duration(dwell_s)}")
        except Exception:
            pass

    if report_age_min is not None:
        parts.append(f"上报于{report_age_min}min前" if report_age_min >= 1 else "刚刚上报")

    # Battery: currentStay.batteryLevel (whereabouts 会在停留内更新) 优先，
    # 其次 batteryObservations[0]（注意：数组最新在前）。取两者中更新的时间戳。
    battery = stay.get("batteryLevel")
    if battery is None:
        obs = data.get("batteryObservations")
        if isinstance(obs, list) and obs:
            first = obs[0]  # latest-first ordering (verified 2026-10-11)
            if isinstance(first, dict):
                battery = first.get("batteryLevel")
    if isinstance(battery, (int, float)) and 0 < battery <= 100:
        parts.append(f"电量{int(battery)}%")

    weather = stay.get("weather")
    if weather:
        parts.append(f"天气{weather}")

    return " · ".join(parts)


# ── Channel detection ────────────────────────────────────────────────────────

def _channel_piece(kwargs: dict) -> str:
    now = time.time()
    session_id = str(kwargs.get("session_id") or "")
    cached = _channel_cache.get(session_id)
    if cached and now - cached[0] < CHANNEL_CACHE_TTL:
        return f"Channel: {cached[1]}"

    channel = _resolve_channel(kwargs)
    _channel_cache[session_id] = (now, channel)
    # 防缓存无限增长：超过 64 个会话时整表重置（网关进程长期运行的卫生措施）
    if len(_channel_cache) > 64:
        _channel_cache.clear()
    return f"Channel: {channel}"


def _resolve_channel(kwargs: dict) -> str:
    # 1) Native platform field (v1.1 primary path).
    platform = str(kwargs.get("platform") or "").strip().lower()
    if platform:
        mapped = _map_platform(platform, kwargs)
        if mapped:
            return mapped

    # 2) Fallback: HermesPilot App session vs desktop, via link.db.
    channel = _channel_from_linkdb(kwargs)
    if channel:
        return channel

    return "unknown"


def _map_platform(platform: str, kwargs: dict) -> Optional[str]:
    """Map the hook's platform value to a channel label.

    NOTE (v1.1 遗留标定项): the exact values emitted per channel are to be
    calibrated on real devices during acceptance testing (§6.2). Known
    candidates from source: "tui" (desktop TUI), gateway platform ids for
    messaging channels (e.g. containing "feishu"/"telegram"/...), and the
    HermesPilot path which forwards through tui_gateway.
    """
    if "feishu" in platform or "lark" in platform:
        return "feishu"
    if platform in ("tui", "desktop", "gui"):
        # tui covers both desktop GUI and TUI; distinguish App via linkdb.
        return _channel_from_linkdb(kwargs) or "desktop"
    # Gateway ids like "telegram", "discord", ... are already channel names.
    if platform and not platform.startswith("tui"):
        return platform
    return None


def _channel_from_linkdb(kwargs: dict) -> Optional[str]:
    """Check whether this hermes session is a HermesPilot App session."""
    session_id = str(kwargs.get("session_id") or "")
    if not session_id:
        return None
    try:
        import sqlite3

        db = os.path.join(os.path.expanduser("~"), ".hermeslink", "link.db")
        if not os.path.isfile(db):
            return None
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        try:
            cur = conn.execute(
                "SELECT conversation_id FROM conversation_stats "
                "WHERE hermes_session_id = ? AND deleted_at IS NULL "
                "ORDER BY updated_at DESC LIMIT 1",
                (session_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        return "hermespilot" if row else None
    except Exception:
        return None


# ── Hook entry ───────────────────────────────────────────────────────────────

def _on_pre_llm_call(**kwargs: Any) -> dict | None:
    try:
        now = datetime.now().astimezone()
        pieces = [f"[Current context: {now.strftime('%Y-%m-%d %H:%M %Z')}"]

        loc = _location_piece(_load_locations() or {}, now.timestamp())
        if loc:
            pieces.append(loc)

        pieces.append(_channel_piece(kwargs))
        context = " | ".join(pieces) + "]"

        return {"context": context}
    except Exception:
        logger.warning("spatial-injector: injection failed; degrading", exc_info=True)
        # Absolute last resort: time-only (still better than nothing, and
        # consistent with the degrade-to-time contract).
        try:
            now = datetime.now().astimezone()
            return {"context": f"[Current context: {now.strftime('%Y-%m-%d %H:%M %Z')} | Channel: unknown]"}
        except Exception:
            return None


def register(ctx: "PluginContext") -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    logger.info("spatial-injector v1.0 registered (pre_llm_call hook)")
