"""
Tarkov Data Auto-Updater — fetches latest game data on app launch.

Uses the tarkov.dev GraphQL API (free, open-source, updates every 5 min)
to fetch current quest info, ammo data, boss spawns, and game status.

The fetched data is cached locally to avoid hitting the API every launch.
Cache expires after 6 hours.
"""

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "tarkov_live_data.json")
_CACHE_MAX_AGE = 6 * 3600  # 6 hours

_API_URL = "https://api.tarkov.dev/graphql"

# GraphQL query for key game data (tarkov.dev schema)
_QUERY = """
{
  tasks(lang: en) {
    name
    trader { name }
    minPlayerLevel
  }
  maps(lang: en) {
    name
    players
    raidDuration
    bosses { name spawnChance }
    extracts { name }
  }
}
"""


def _fetch_api_data() -> Optional[dict]:
    """Fetch latest data from tarkov.dev GraphQL API."""
    try:
        import urllib.request
        import ssl

        # Try certifi first, fall back to unverified context (macOS issue)
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            _API_URL,
            data=json.dumps({"query": _QUERY}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "PMCOverwatch/1.0",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "data" in data:
                return data["data"]
            logger.warning("tarkov.dev returned unexpected format")
    except Exception as e:
        logger.warning("Failed to fetch tarkov.dev data: %s", e)
    return None


def _format_live_data(data: dict) -> str:
    """Format the API data into a compact text block for LLM injection."""
    lines = []

    # Map info with bosses
    if data.get("maps"):
        lines.append("\n=== LIVE MAP DATA ===")
        for m in data["maps"]:
            name = m.get("name", "?")
            players = m.get("players", "?")
            duration = m.get("raidDuration", "?")
            extracts = m.get("extracts", [])
            bosses = m.get("bosses", [])
            line = f"{name}: {players} players, {duration}min raid, {len(extracts)} extracts"
            if bosses:
                boss_info = ", ".join(
                    f"{b.get('name', '?')} ({b.get('spawnChance', 0)*100:.0f}%)"
                    for b in bosses
                )
                line += f" | Bosses: {boss_info}"
            lines.append(line)

    # Quest count per trader
    if data.get("tasks"):
        lines.append("\n=== QUEST COUNT BY TRADER ===")
        trader_counts: dict[str, int] = {}
        for task in data["tasks"]:
            trader = task.get("trader", {}).get("name", "Unknown")
            trader_counts[trader] = trader_counts.get(trader, 0) + 1
        for trader, count in sorted(trader_counts.items(), key=lambda x: -x[1]):
            lines.append(f"{trader}: {count} quests")
        lines.append(f"Total: {len(data['tasks'])} quests in game")

    return "\n".join(lines)


def get_live_data() -> str:
    """Get live Tarkov data (cached, refreshes every 6 hours).

    Returns a formatted string ready for LLM context injection,
    or an empty string if unavailable.
    """
    os.makedirs(_CACHE_DIR, exist_ok=True)

    # Check cache
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, "r") as f:
                cached = json.load(f)
            age = time.time() - cached.get("timestamp", 0)
            if age < _CACHE_MAX_AGE and cached.get("formatted"):
                logger.info("Using cached tarkov.dev data (%.1fh old)", age / 3600)
                return cached["formatted"]
        except Exception:
            pass

    # Fetch fresh data
    logger.info("Fetching latest data from tarkov.dev...")
    data = _fetch_api_data()
    if not data:
        # Try to use stale cache if API is down
        if os.path.exists(_CACHE_FILE):
            try:
                with open(_CACHE_FILE, "r") as f:
                    cached = json.load(f)
                if cached.get("formatted"):
                    logger.info("API unavailable, using stale cache")
                    return cached["formatted"]
            except Exception:
                pass
        return ""

    formatted = _format_live_data(data)

    # Save to cache
    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump({"timestamp": time.time(), "formatted": formatted}, f)
        logger.info("Cached tarkov.dev data (%d chars)", len(formatted))
    except Exception:
        pass

    return formatted


# Standalone test
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    result = get_live_data()
    if result:
        print(result)
    else:
        print("No data available")
