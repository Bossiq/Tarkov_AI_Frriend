"""
Avatar 3D — pywebview-based 3D holographic avatar window.

Manages a **separate process** running pywebview (required on macOS
because Cocoa needs the main thread, but tkinter already owns it).

Communication: Python main process → avatar subprocess via a pipe.

Usage:
    avatar = Avatar3D()
    avatar.start()              # launches the 3D window in a subprocess
    avatar.set_mouth(0.7)       # amplitude 0.0–1.0
    avatar.set_emotion("happy") # see VALID_EMOTIONS
    avatar.set_mode("speaking") # idle/listening/thinking/speaking
    avatar.stop()               # close the window
"""

import logging
import multiprocessing
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

VALID_EMOTIONS = {
    "neutral", "happy", "excited", "amused", "sarcastic",
    "surprised", "concerned", "curious", "confident",
    "empathetic", "focused", "alarmed",
}

VALID_MODES = {"idle", "listening", "thinking", "speaking"}
VALID_ANIMATIONS = {
    "idle", "wave", "clap", "think", "point", "shrug",
    "celebrate", "salute", "nod", "headShake", "bow",
    "crossArms", "facepalm", "dance", "laugh", "thumbsUp",
    "idle2", "look_around", "weight_shift",
}
# Backward compat
VALID_GESTURES = VALID_ANIMATIONS

_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


# ══════════════════════════════════════════════════════════════════
#  Subprocess target — runs pywebview on its own main thread
# ══════════════════════════════════════════════════════════════════
def _avatar_process(pipe_conn, project_root, width, height, title):
    """Entry point for the avatar subprocess.

    Starts a local HTTP server so VRM textures load correctly
    (file:// protocol blocks embedded texture loading in WebKit).
    """
    import webview
    import threading
    import http.server

    # Silent HTTP handler serving the project root
    class _SilentHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=project_root, **kwargs)
        def log_message(self, format, *args):
            pass  # silence HTTP logs

    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()

    httpd = http.server.HTTPServer(('127.0.0.1', port), _SilentHandler)
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    url = f"{base_url}/assets/avatar_3d.html?model={base_url}/models/avatar.vrm"

    window = webview.create_window(
        title,
        url=url,
        width=width,
        height=height,
        frameless=True,
        on_top=True,
        transparent=True,
        background_color='#00000000',
    )

    def _command_listener():
        """Listen for commands from the parent process and execute JS."""
        # Wait for window to load
        while not window.dom:
            time.sleep(0.1)

        # Signal ready
        try:
            pipe_conn.send({"type": "ready"})
        except Exception:
            return

        while True:
            try:
                if pipe_conn.poll(0.016):  # ~60 FPS check rate
                    msg = pipe_conn.recv()
                    if msg is None or msg.get("type") == "stop":
                        window.destroy()
                        break
                    js = msg.get("js")
                    if js:
                        try:
                            window.evaluate_js(js)
                        except Exception:
                            pass
            except (EOFError, BrokenPipeError, OSError):
                break

    # Start command listener on a thread BEFORE webview.start()
    listener = threading.Thread(target=_command_listener, daemon=True)
    listener.start()

    # webview.start() blocks (runs the Cocoa event loop on main thread)
    webview.start(debug=False)


class Avatar3D:
    """Manages a subprocess running the 3D holographic VRM avatar."""

    def __init__(
        self,
        width: int = 500,
        height: int = 600,
        title: str = "PMC Overwatch — Avatar",
    ) -> None:
        self._width = width
        self._height = height
        self._title = title
        self._process: Optional[multiprocessing.Process] = None
        self._pipe: Optional[multiprocessing.Connection] = None
        self._running = False

    def start(self) -> bool:
        """Launch the 3D avatar in a subprocess.

        Returns True if started successfully.
        """
        try:
            import webview  # noqa: F401
        except ImportError:
            logger.warning("pywebview not installed — 3D avatar disabled")
            return False

        html_path = os.path.join(_ASSET_DIR, "avatar_3d.html")
        model_path = os.path.join(_MODEL_DIR, "avatar.vrm")
        project_root = os.path.dirname(os.path.abspath(__file__))

        if not os.path.exists(html_path):
            logger.error("avatar_3d.html not found at %s", html_path)
            return False
        if not os.path.exists(model_path):
            logger.error("avatar.vrm not found at %s", model_path)
            return False

        if self._running:
            return True

        # Create pipe for communication
        parent_conn, child_conn = multiprocessing.Pipe()
        self._pipe = parent_conn

        self._process = multiprocessing.Process(
            target=_avatar_process,
            args=(child_conn, project_root,
                  self._width, self._height, self._title),
            daemon=True,
        )
        self._process.start()

        # Wait for "ready" signal (max 20s for model loading)
        try:
            if parent_conn.poll(20.0):
                msg = parent_conn.recv()
                if msg and msg.get("type") == "ready":
                    self._running = True
                    logger.info("3D avatar process started (pid=%d)", self._process.pid)
                    return True
        except Exception:
            pass

        logger.warning("3D avatar did not become ready in time")
        self.stop()
        return False

    def _send(self, js: str) -> None:
        """Send a JS command to the avatar process."""
        if not self._running or not self._pipe:
            return
        try:
            self._pipe.send({"js": js})
        except (BrokenPipeError, OSError):
            self._running = False

    # ── Public API ──────────────────────────────────────────────────

    def set_mouth(self, amplitude: float) -> None:
        """Set mouth openness (0.0 = closed, 1.0 = wide open)."""
        amp = max(0.0, min(1.0, amplitude))
        self._send(f"window.setMouth({amp:.3f})")

    def set_emotion(self, emotion: str) -> None:
        """Set facial expression."""
        if emotion not in VALID_EMOTIONS:
            emotion = "neutral"
        self._send(f'window.setEmotion("{emotion}")')

    def set_mode(self, mode: str) -> None:
        """Set avatar mode (changes glow color)."""
        if mode not in VALID_MODES:
            mode = "idle"
        self._send(f'window.setMode("{mode}")')

    def play_animation(self, name: str) -> None:
        """Play a named Mixamo animation with crossfade blending."""
        if name not in VALID_ANIMATIONS:
            logger.warning("Unknown animation: %s", name)
            return
        self._send(f'window.playAnimation("{name}")')

    def set_gesture(self, gesture: str) -> None:
        """Legacy: trigger a gesture (maps to play_animation)."""
        self.play_animation(gesture)

    def set_head_tilt(self, x: float, y: float) -> None:
        """Tilt head by (x, y) degrees."""
        self._send(f"window.setHeadTilt({x:.2f}, {y:.2f})")

    def is_ready(self) -> bool:
        """Check if the 3D avatar is loaded and ready."""
        return self._running and self._process is not None and self._process.is_alive()

    def stop(self) -> None:
        """Close the 3D avatar subprocess."""
        if self._pipe:
            try:
                self._pipe.send({"type": "stop"})
            except Exception:
                pass
            self._pipe = None
        if self._process and self._process.is_alive():
            self._process.join(timeout=3.0)
            if self._process.is_alive():
                self._process.terminate()
        self._process = None
        self._running = False

    @property
    def available(self) -> bool:
        """Check if pywebview is installed."""
        try:
            import webview  # noqa: F401
            return True
        except ImportError:
            return False


# ── Standalone test ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)

    avatar = Avatar3D()
    if not avatar.start():
        print("Failed to start 3D avatar")
        sys.exit(1)

    print("3D Avatar running. Testing animations...")
    time.sleep(3)

    # Test mode transitions
    for mode in ["listening", "thinking", "speaking", "idle"]:
        print(f"  Mode: {mode}")
        avatar.set_mode(mode)
        time.sleep(2)

    # Test expressions
    for emotion in ["happy", "surprised", "concerned", "excited", "neutral"]:
        print(f"  Emotion: {emotion}")
        avatar.set_emotion(emotion)
        time.sleep(1.5)

    # Test mouth movement
    print("  Testing mouth sync...")
    import random
    for _ in range(60):
        avatar.set_mouth(random.uniform(0.0, 1.0))
        time.sleep(0.05)
    avatar.set_mouth(0.0)

    print("Test complete. Close the avatar window or Ctrl+C.")
    try:
        while avatar.is_ready():
            time.sleep(1)
    except KeyboardInterrupt:
        avatar.stop()
