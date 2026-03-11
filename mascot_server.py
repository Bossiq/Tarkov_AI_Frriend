"""
Mascot Server — FastAPI + WebSocket bridge for the stream mascot.

Runs on localhost:8420 (same port as old dashboard).
Serves the mascot HTML overlay and pushes real-time state updates
via WebSocket to the OBS Browser Source.

Endpoints:
  GET  /              — serves the dashboard / control panel HTML
  GET  /mascot        — serves the mascot overlay HTML for OBS
  GET  /api/config    — returns current config (API keys sanitized)
  PUT  /api/config    — updates config values at runtime + writes .env
  GET  /api/status    — returns app status
  POST /api/clear-memory — clears AI conversation memory
  WS   /ws/mascot     — WebSocket pushes mascot state in real-time

State messages (JSON over WebSocket):
  {"type": "mode",      "value": "idle"|"listening"|"thinking"|"speaking"}
  {"type": "amplitude", "value": 0.0-1.0}
  {"type": "emotion",   "value": "neutral"|"happy"|"excited"|...}
  {"type": "chat",      "value": {"user": "...", "message": "..."}}
  {"type": "navigate",  "value": "left"|"right"|"center"|"random"}
  {"type": "animation", "value": "dance"|"celebrate"|"die"|"sleep"|...}
  {"type": "subtitle",  "value": "text currently being spoken"}

SECURITY: only binds to 127.0.0.1 — NOT exposed to the internet.
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    logger.info("fastapi/uvicorn not available — mascot server disabled")

# Path constants
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_FILE = os.path.join(_BASE_DIR, ".env")
_MASCOT_HTML = os.path.join(_BASE_DIR, "assets", "mascot.html")
_MASCOT_3D_HTML = os.path.join(_BASE_DIR, "assets", "mascot_3d.html")
_DASHBOARD_HTML = os.path.join(_BASE_DIR, "assets", "dashboard_ui.html")

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


class MascotServer:
    """WebSocket-powered mascot server for OBS Browser Source integration.

    Replaces the old Dashboard + Avatar3D with a single unified server
    that pushes real-time state updates to the mascot overlay.
    """

    def __init__(
        self,
        port: int = 8420,
        get_status: Optional[Callable] = None,
        clear_memory: Optional[Callable] = None,
        on_config_change: Optional[Callable] = None,
    ) -> None:
        self._port = port
        self._get_status = get_status
        self._clear_memory = clear_memory
        self._on_config_change = on_config_change
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[uvicorn.Server] = None
        self._start_time = time.monotonic()

        # Connected WebSocket clients
        self._clients: list[WebSocket] = []
        self._clients_lock = threading.Lock()

        # Current mascot state (cached for new connections)
        self._state = {
            "mode": "idle",
            "amplitude": 0.0,
            "emotion": "neutral",
            "subtitle": "",
        }

        # Asyncio loop for broadcast (set when server starts)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def available(self) -> bool:
        return _FASTAPI_AVAILABLE

    # ── Lifecycle ─────────────────────────────────────────────────────
    def start(self) -> bool:
        """Start the mascot server on a background thread."""
        if not _FASTAPI_AVAILABLE:
            logger.warning("Cannot start mascot server — fastapi/uvicorn not installed")
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
            target=self._run_server, name="MascotServer", daemon=True
        )
        self._thread.start()
        logger.info("Mascot server running at http://127.0.0.1:%d", self._port)
        logger.info("OBS Browser Source URL: http://127.0.0.1:%d/mascot", self._port)
        return True

    def _run_server(self) -> None:
        """Run the uvicorn server with its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        loop.run_until_complete(self._server.serve())

    def stop(self) -> None:
        """Stop the mascot server."""
        if self._server is not None:
            self._server.should_exit = True
            if self._thread is not None:
                self._thread.join(timeout=3.0)
        logger.info("Mascot server stopped")

    # ── State Broadcasting ────────────────────────────────────────────
    def set_mode(self, mode: str) -> None:
        """Update mascot mode (idle, listening, thinking, speaking)."""
        self._state["mode"] = mode
        self._broadcast({"type": "mode", "value": mode})

    def set_amplitude(self, amplitude: float) -> None:
        """Update speaking amplitude (0.0-1.0) for lip sync."""
        self._state["amplitude"] = amplitude
        self._broadcast({"type": "amplitude", "value": round(amplitude, 3)})

    def set_emotion(self, emotion: str) -> None:
        """Update mascot emotion/expression."""
        self._state["emotion"] = emotion
        self._broadcast({"type": "emotion", "value": emotion})

    def set_subtitle(self, text: str) -> None:
        """Update subtitle text (what the AI is currently saying)."""
        self._state["subtitle"] = text
        self._broadcast({"type": "subtitle", "value": text})

    def send_chat_event(self, user: str, message: str) -> None:
        """Forward a Twitch chat message to the mascot for reaction."""
        self._broadcast({
            "type": "chat",
            "value": {"user": user, "message": message},
        })

    def send_animation(self, animation: str) -> None:
        """Trigger a specific mascot animation."""
        self._broadcast({"type": "animation", "value": animation})

    def send_navigate(self, direction: str) -> None:
        """Move the mascot to a position (left, right, center, random)."""
        self._broadcast({"type": "navigate", "value": direction})

    def _broadcast(self, message: dict) -> None:
        """Broadcast a JSON message to all connected WebSocket clients."""
        if not self._loop or not self._clients:
            return

        data = json.dumps(message)

        async def _send_to_all():
            with self._clients_lock:
                dead = []
                for ws in self._clients:
                    try:
                        await ws.send_text(data)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self._clients.remove(ws)

        try:
            asyncio.run_coroutine_threadsafe(_send_to_all(), self._loop)
        except Exception:
            pass  # Server shutting down

    # ── FastAPI App ───────────────────────────────────────────────────
    def _create_app(self) -> "FastAPI":
        """Build the FastAPI application with all routes."""
        app = FastAPI(title="PMC Overwatch Mascot Server", docs_url=None, redoc_url=None)

        # Serve static assets
        assets_dir = os.path.join(_BASE_DIR, "assets")
        if os.path.isdir(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        sprites_dir = os.path.join(_BASE_DIR, "assets", "mascot_sprites")
        if os.path.isdir(sprites_dir):
            app.mount("/sprites", StaticFiles(directory=sprites_dir), name="sprites")

        models_dir = os.path.join(_BASE_DIR, "models")
        if os.path.isdir(models_dir):
            app.mount("/models", StaticFiles(directory=models_dir), name="models")

        @app.get("/", response_class=HTMLResponse)
        async def serve_dashboard():
            if os.path.exists(_DASHBOARD_HTML):
                with open(_DASHBOARD_HTML, "r", encoding="utf-8") as f:
                    return f.read()
            return HTMLResponse(
                "<h1>PMC Overwatch</h1>"
                "<p>Dashboard UI not found. Place dashboard_ui.html in assets/</p>",
                status_code=200,
            )

        @app.get("/mascot", response_class=HTMLResponse)
        async def serve_mascot():
            # Serve 3D version if available, fall back to 2D
            target = _MASCOT_3D_HTML if os.path.exists(_MASCOT_3D_HTML) else _MASCOT_HTML
            if os.path.exists(target):
                with open(target, "r", encoding="utf-8") as f:
                    return f.read()
            return HTMLResponse(
                "<h1>Mascot Not Found</h1>"
                "<p>Place mascot_3d.html or mascot.html in assets/</p>",
                status_code=200,
            )

        @app.get("/mascot2d", response_class=HTMLResponse)
        async def serve_mascot_2d():
            if os.path.exists(_MASCOT_HTML):
                with open(_MASCOT_HTML, "r", encoding="utf-8") as f:
                    return f.read()
            return HTMLResponse("<h1>2D Mascot Not Found</h1>", status_code=200)

        @app.websocket("/ws/mascot")
        async def mascot_ws(websocket: WebSocket):
            await websocket.accept()
            with self._clients_lock:
                self._clients.append(websocket)
            logger.info("Mascot client connected (%d total)", len(self._clients))

            # Send current state to new client
            try:
                for key, value in self._state.items():
                    await websocket.send_text(json.dumps({
                        "type": key, "value": value
                    }))
            except Exception:
                pass

            try:
                while True:
                    # Listen for commands FROM the mascot (chat triggers)
                    data = await websocket.receive_text()
                    try:
                        msg = json.loads(data)
                        logger.debug("Mascot sent: %s", msg)
                    except json.JSONDecodeError:
                        pass
            except WebSocketDisconnect:
                pass
            finally:
                with self._clients_lock:
                    if websocket in self._clients:
                        self._clients.remove(websocket)
                logger.info("Mascot client disconnected (%d remaining)", len(self._clients))

        @app.get("/api/config")
        async def get_config():
            config = _read_env_dict()
            return _sanitize_config(config)

        @app.put("/api/config")
        async def update_config(updates: dict):
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
                "mascot_clients": len(self._clients),
                "current_state": self._state,
            }
            if self._get_status:
                try:
                    status.update(self._get_status())
                except Exception:
                    logger.exception("Status callback error")
            return status

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

    server = MascotServer(port=8420)
    if server.start():
        print(f"Mascot server running at http://127.0.0.1:8420")
        print(f"OBS Browser Source: http://127.0.0.1:8420/mascot")
        print("Press Ctrl+C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.stop()
    else:
        print("Could not start mascot server")
