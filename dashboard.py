"""
Web Dashboard — FastAPI settings panel for PMC Overwatch.

Runs on localhost:8420 on a daemon thread.  Provides a web-based UI
for changing configuration, viewing logs, and monitoring system status
without editing .env manually.

Endpoints:
  GET  /           — serves the dashboard HTML
  GET  /api/config — returns current config (API keys sanitized)
  PUT  /api/config — updates config values at runtime + writes .env
  GET  /api/status — returns app status
  GET  /api/logs   — returns last 100 log lines
  POST /api/clear-memory — clears AI conversation memory

SECURITY: only binds to 127.0.0.1 — NOT exposed to the internet.
"""

import logging
import os
import re
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    logger.info("fastapi/uvicorn not available — dashboard disabled")


# Path to the .env file
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_DASHBOARD_HTML = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets", "dashboard_ui.html"
)

# Keys that should be sanitized in the API response
_SENSITIVE_KEYS = {"GROQ_API_KEY", "GEMINI_API_KEY", "TWITCH_TOKEN"}


def _read_env_dict() -> dict[str, str]:
    """Read the .env file into a dict."""
    result = {}
    if not os.path.exists(_ENV_FILE):
        return result
    with open(_ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
            if match:
                result[match.group(1)] = match.group(2)
    return result


def _write_env_dict(updates: dict[str, str]) -> None:
    """Update specific keys in the .env file while preserving comments/order."""
    lines = []
    if os.path.exists(_ENV_FILE):
        with open(_ENV_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", stripped)
        if match and match.group(1) in updates:
            key = match.group(1)
            new_lines.append(f"{key}={updates[key]}\n")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    # Append any new keys not already in the file
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}\n")

    with open(_ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def _sanitize_config(config: dict[str, str]) -> dict[str, str]:
    """Replace sensitive API keys with masked values."""
    sanitized = {}
    for key, value in config.items():
        if key in _SENSITIVE_KEYS and value:
            sanitized[key] = value[:4] + "***" + value[-4:] if len(value) > 8 else "***"
        else:
            sanitized[key] = value
    return sanitized


class Dashboard:
    """Web dashboard for PMC Overwatch configuration."""

    def __init__(
        self,
        port: int = 8420,
        get_status: Optional[Callable] = None,
        get_logs: Optional[Callable] = None,
        clear_memory: Optional[Callable] = None,
        on_config_change: Optional[Callable] = None,
    ) -> None:
        self._port = port
        self._get_status = get_status
        self._get_logs = get_logs
        self._clear_memory = clear_memory
        self._on_config_change = on_config_change
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[uvicorn.Server] = None
        self._start_time = time.monotonic()

    @property
    def available(self) -> bool:
        return _FASTAPI_AVAILABLE

    def start(self) -> bool:
        """Start the dashboard on a background thread."""
        if not _FASTAPI_AVAILABLE:
            logger.warning("Cannot start dashboard — fastapi/uvicorn not installed")
            return False

        app = self._create_app()
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)

        self._thread = threading.Thread(
            target=self._server.run, name="Dashboard", daemon=True
        )
        self._thread.start()
        logger.info("Dashboard running at http://127.0.0.1:%d", self._port)
        return True

    def stop(self) -> None:
        """Stop the dashboard server."""
        if self._server is not None:
            self._server.should_exit = True
            if self._thread is not None:
                self._thread.join(timeout=3.0)
        logger.info("Dashboard stopped")

    def _create_app(self) -> "FastAPI":
        """Build the FastAPI application with all routes."""
        app = FastAPI(title="PMC Overwatch Dashboard", docs_url=None, redoc_url=None)

        @app.get("/", response_class=HTMLResponse)
        async def serve_dashboard():
            if os.path.exists(_DASHBOARD_HTML):
                with open(_DASHBOARD_HTML, "r", encoding="utf-8") as f:
                    return f.read()
            return HTMLResponse(
                "<h1>PMC Overwatch Dashboard</h1>"
                "<p>Dashboard UI not found. Place dashboard_ui.html in assets/</p>",
                status_code=200,
            )

        @app.get("/api/config")
        async def get_config():
            config = _read_env_dict()
            return _sanitize_config(config)

        @app.put("/api/config")
        async def update_config(updates: dict):
            # Filter out sanitized values (don't overwrite keys with ***)
            real_updates = {}
            for key, value in updates.items():
                if "***" not in str(value):
                    real_updates[key] = str(value)
                    os.environ[key] = str(value)

            if real_updates:
                _write_env_dict(real_updates)
                if self._on_config_change:
                    try:
                        self._on_config_change(real_updates)
                    except Exception:
                        logger.exception("Config change callback error")

            return {"status": "ok", "updated": list(real_updates.keys())}

        @app.get("/api/status")
        async def get_status():
            uptime = time.monotonic() - self._start_time
            status = {
                "uptime_seconds": round(uptime, 1),
                "uptime_human": _format_uptime(uptime),
            }
            if self._get_status:
                try:
                    status.update(self._get_status())
                except Exception:
                    logger.exception("Status callback error")
            return status

        @app.get("/api/logs")
        async def get_logs():
            if self._get_logs:
                try:
                    logs = self._get_logs()
                    return {"logs": logs}
                except Exception:
                    return {"logs": []}
            return {"logs": []}

        @app.post("/api/clear-memory")
        async def clear_memory():
            if self._clear_memory:
                try:
                    self._clear_memory()
                    return {"status": "ok", "message": "Memory cleared"}
                except Exception:
                    return JSONResponse(
                        {"status": "error", "message": "Failed to clear memory"},
                        status_code=500,
                    )
            return {"status": "ok", "message": "No brain connected"}

        return app


def _format_uptime(seconds: float) -> str:
    """Format seconds into human-readable uptime."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs}h {mins}m {secs}s"
    elif mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()

    dash = Dashboard(port=8420)
    if dash.start():
        print(f"Dashboard running at http://127.0.0.1:8420")
        print("Press Ctrl+C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            dash.stop()
    else:
        print("Could not start dashboard")
