"""
Video Capture — optional webcam frame grabber.

OpenCV is imported lazily so the application still launches even if
``opencv-python`` is missing or the camera is unavailable.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import cv2

    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False
    logger.info("opencv-python not available — video capture disabled")


class VideoCapture:
    """Manages a single camera device for frame capture."""

    def __init__(self, camera_index: int = 0) -> None:
        self._camera_index = camera_index
        self._cap: Optional[object] = None  # cv2.VideoCapture when active

    # ── Lifecycle ─────────────────────────────────────────────────────
    def start(self) -> bool:
        """Open the camera.  Returns True on success."""
        if not _CV2_AVAILABLE:
            logger.warning("Cannot start — opencv-python is not installed")
            return False
        self._cap = cv2.VideoCapture(self._camera_index)
        if not self._cap.isOpened():
            logger.error("Could not open video device %d", self._camera_index)
            self._cap.release()
            self._cap = None
            return False
        logger.info("Video capture started (device %d)", self._camera_index)
        return True

    def stop(self) -> None:
        """Release camera resources."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("Video capture stopped")

    # ── Frame capture ─────────────────────────────────────────────────
    def get_frame(self):
        """Read a single frame.  Returns the frame or None."""
        if self._cap is None:
            return None
        ret, frame = self._cap.read()
        if ret:
            return frame
        logger.warning("Failed to read frame from camera")
        return None

    # ── Cleanup ───────────────────────────────────────────────────────
    def __del__(self) -> None:
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

    with VideoCapture() as vc:
        frame = vc.get_frame()
        if frame is not None:
            print(f"Captured frame: {frame.shape}")
        else:
            print("No frame captured (camera may not be available)")
