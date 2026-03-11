"""
Screen Capture — anti-cheat safe display capture for AI vision.

Uses the ``mss`` library for cross-platform screen capture:
  • Windows: DXGI Desktop Duplication API (same as OBS Display Capture)
  • macOS:   Core Graphics / Quartz (same as macOS screenshot)
  • Linux:   X11/XCB screen grab

This is NOT game-process injection; it uses standard OS screenshot APIs.

Safety guarantee (Windows / BattlEye):
  • Does NOT inject into the game process
  • Does NOT read game memory or modify game files
  • Does NOT hook any DLLs or use kernel drivers
  • Identical to OBS Display Capture — used by every Tarkov streamer

BattlEye FAQ confirms: "We only ever ban for the use of actual
cheats/hacks. Non-cheat overlays are generally supported."

Note for macOS:
  • Screen Recording permission must be granted in
    System Settings → Privacy & Security → Screen Recording
  • mss will prompt for permission on first run

Features:
  • Background capture thread at configurable FPS (default 1 FPS)
  • Keeps only the latest frame (no buffer accumulation)
  • Auto-resize to 720p for minimal bandwidth to AI
  • JPEG encoding for efficient API upload
  • Graceful fallback if mss is unavailable
"""

import io
import logging
import os
import platform
import tempfile
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import mss
    import mss.tools
    _MSS_AVAILABLE = True
except ImportError:
    _MSS_AVAILABLE = False
    logger.info("mss not available — screen capture disabled")

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# Defaults
_DEFAULT_MONITOR = 1       # Primary monitor (0 = all monitors combined)
_DEFAULT_FPS = 1.0         # 1 frame per second
_DEFAULT_RESIZE = (1280, 720)
_JPEG_QUALITY = 70         # Balance between quality and size


class ScreenCapture:
    """Cross-platform screen capture (Windows/macOS/Linux).

    Uses mss library: DXGI on Windows, Quartz on macOS, X11 on Linux.
    Thread-safe: runs a background capture loop that stores only the
    latest frame.  Call ``get_latest_frame()`` from any thread to get
    the most recent screenshot as JPEG bytes.
    """

    def __init__(
        self,
        monitor: int = _DEFAULT_MONITOR,
        fps: float = _DEFAULT_FPS,
        resize: tuple[int, int] = _DEFAULT_RESIZE,
        shutdown_event: Optional[threading.Event] = None,
    ) -> None:
        self._monitor = monitor
        self._fps = max(0.1, fps)
        self._resize = resize
        self._shutdown = shutdown_event or threading.Event()

        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Latest frame storage (thread-safe via lock)
        self._lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None
        self._latest_timestamp: float = 0.0
        self._frame_count: int = 0
        self._temp_path = os.path.join(
            tempfile.gettempdir(), "pmc_overwatch_screen.jpg"
        )

    @property
    def available(self) -> bool:
        """True if screen capture is available (mss + PIL installed)."""
        return _MSS_AVAILABLE and _PIL_AVAILABLE

    @property
    def frame_count(self) -> int:
        return self._frame_count

    # ── Lifecycle ─────────────────────────────────────────────────────
    def start(self) -> bool:
        """Start background capture thread.  Returns True on success."""
        if not self.available:
            logger.warning("Cannot start screen capture — mss or Pillow not installed")
            return False
        if self._running:
            return True

        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop, name="ScreenCapture", daemon=True
        )
        self._thread.start()
        logger.info(
            "Screen capture started (monitor=%d, fps=%.1f, resize=%s)",
            self._monitor, self._fps, self._resize,
        )
        return True

    def stop(self) -> None:
        """Stop the capture thread."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        # Clean up temp file
        try:
            if os.path.exists(self._temp_path):
                os.remove(self._temp_path)
        except OSError:
            pass
        logger.info("Screen capture stopped (%d frames captured)", self._frame_count)

    # ── Capture loop ──────────────────────────────────────────────────
    def _capture_loop(self) -> None:
        """Background loop that captures frames at the configured FPS."""
        interval = 1.0 / self._fps

        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if self._monitor >= len(monitors):
                    logger.error(
                        "Monitor %d not found (available: %d). Falling back to 1.",
                        self._monitor, len(monitors) - 1,
                    )
                    self._monitor = 1

                while self._running and not self._shutdown.is_set():
                    start = time.monotonic()
                    try:
                        self._capture_frame(sct)
                    except Exception:
                        logger.exception("Screen capture frame error")

                    # Sleep remaining time
                    elapsed = time.monotonic() - start
                    sleep_time = max(0.01, interval - elapsed)
                    if self._shutdown.wait(timeout=sleep_time):
                        break

        except Exception:
            logger.exception("Screen capture loop fatal error")
        finally:
            self._running = False

    def _capture_frame(self, sct) -> None:
        """Capture a single frame, resize, and store as JPEG."""
        monitor = sct.monitors[self._monitor]
        raw = sct.grab(monitor)

        # Convert to PIL Image using cross-platform approach:
        # mss raw data format varies by OS, so we use PIL's frombytes
        # with the mss-provided size and raw BGRA data
        try:
            # mss provides .bgra on all platforms (despite BGRA naming,
            # the actual byte order is correct for the current OS)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except AttributeError:
            # Fallback for older mss versions: use rgb attribute
            try:
                img = Image.frombytes("RGB", raw.size, raw.rgb)
            except Exception:
                # Last resort: save via mss tools and re-open
                png_bytes = mss.tools.to_png(raw.rgb, raw.size)
                img = Image.open(io.BytesIO(png_bytes)).convert("RGB")

        # Resize for AI analysis
        if self._resize and img.size != self._resize:
            img = img.resize(self._resize, Image.LANCZOS)

        # Encode as JPEG
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
        jpeg_bytes = buf.getvalue()
        buf.close()
        img.close()  # Free PIL image memory immediately

        # Store (thread-safe)
        with self._lock:
            self._latest_jpeg = jpeg_bytes
            self._latest_timestamp = time.monotonic()
            self._frame_count += 1

    # ── Public API ────────────────────────────────────────────────────
    def get_latest_frame(self) -> Optional[bytes]:
        """Get the latest captured frame as JPEG bytes.

        Returns None if no frame has been captured yet.
        Thread-safe — can be called from any thread.
        """
        with self._lock:
            return self._latest_jpeg

    def get_latest_frame_path(self) -> Optional[str]:
        """Save the latest frame to a temp file and return its path.

        Useful for APIs that require a file path.
        Returns None if no frame available.
        """
        with self._lock:
            if self._latest_jpeg is None:
                return None
            try:
                with open(self._temp_path, "wb") as f:
                    f.write(self._latest_jpeg)
                return self._temp_path
            except OSError:
                logger.warning("Could not save frame to %s", self._temp_path)
                return None

    def get_frame_age(self) -> float:
        """Seconds since the latest frame was captured."""
        with self._lock:
            if self._latest_timestamp == 0:
                return float("inf")
            return time.monotonic() - self._latest_timestamp

    # ── Cleanup ───────────────────────────────────────────────────────
    def __del__(self) -> None:
        self.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


# ── Legacy compatibility wrapper ─────────────────────────────────────
class VideoCapture:
    """Legacy webcam capture (kept for backward compatibility).

    Use ScreenCapture for the new screen capture feature.
    """

    def __init__(self, camera_index: int = 0) -> None:
        self._camera_index = camera_index
        self._cap = None

    def start(self) -> bool:
        try:
            import cv2
            self._cap = cv2.VideoCapture(self._camera_index)
            if not self._cap.isOpened():
                self._cap.release()
                self._cap = None
                return False
            return True
        except ImportError:
            return False

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def get_frame(self):
        if self._cap is None:
            return None
        ret, frame = self._cap.read()
        return frame if ret else None

    def __del__(self):
        self.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()

    print("Testing ScreenCapture...")
    with ScreenCapture(fps=2.0) as sc:
        time.sleep(3)
        frame = sc.get_latest_frame()
        if frame:
            print(f"Captured {sc.frame_count} frames, latest: {len(frame):,} bytes")
            path = sc.get_latest_frame_path()
            print(f"Saved to: {path}")
        else:
            print("No frames captured (mss may not be available)")
    print("ScreenCapture test complete")
