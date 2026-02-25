"""
PMC Overwatch GUI — dark-mode desktop interface built with customtkinter.

Provides:
- Start / Stop toggle button
- Scrollable real-time log with timestamps
- Status bar
- Graceful shutdown via WM_DELETE_WINDOW protocol
"""

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)


class OverwatchGUI(ctk.CTk):
    """Main application window for PMC Overwatch."""

    def __init__(self) -> None:
        super().__init__()

        # ── Window setup ──────────────────────────────────────────────
        self.title("PMC Overwatch — Tarkov AI")
        self.geometry("720x600")
        self.minsize(520, 420)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # ── State ─────────────────────────────────────────────────────
        self._toggle_callback: Optional[Callable[[bool], None]] = None
        self._is_running = False

        # Shutdown coordination
        self.shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []

        # ── Build UI ──────────────────────────────────────────────────
        self._build_header()
        self._build_log()
        self._build_footer()

        # Intercept window close for graceful shutdown
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ── Header ────────────────────────────────────────────────────────
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(18, 0))

        ctk.CTkLabel(
            header,
            text="⚔  PMC Overwatch",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")

        self._toggle_btn = ctk.CTkButton(
            header,
            text="▶  Start Overwatch",
            width=180,
            height=38,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#2b8a3e",
            hover_color="#237032",
            command=self._on_toggle,
        )
        self._toggle_btn.pack(side="right")

    # ── Log ───────────────────────────────────────────────────────────
    def _build_log(self) -> None:
        self._log_box = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Menlo", size=13),
            corner_radius=8,
            state="disabled",
            wrap="word",
        )
        self._log_box.pack(fill="both", expand=True, padx=20, pady=12)

    # ── Footer ────────────────────────────────────────────────────────
    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(0, 14))

        self._status_label = ctk.CTkLabel(
            footer,
            text="Status: Offline",
            font=ctk.CTkFont(size=13),
            text_color="#adb5bd",
        )
        self._status_label.pack(side="left")

    # ── Public API (thread-safe) ──────────────────────────────────────
    def set_toggle_callback(self, callback: Callable[[bool], None]) -> None:
        """Register a callback invoked when the user clicks the toggle."""
        self._toggle_callback = callback

    def register_thread(self, thread: threading.Thread) -> None:
        """Track a background thread for shutdown coordination."""
        self._threads.append(thread)

    def log(self, message: str) -> None:
        """Append a timestamped message to the log.  Thread-safe."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}]  {message}\n"
        self.after(0, self._append_log, line)

    def set_status(self, text: str) -> None:
        """Update the status label.  Thread-safe."""
        self.after(0, self._update_status, text)

    # ── Internal ──────────────────────────────────────────────────────
    def _append_log(self, line: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", line)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _update_status(self, text: str) -> None:
        self._status_label.configure(text=f"Status: {text}")

    def _on_toggle(self) -> None:
        self._is_running = not self._is_running

        if self._is_running:
            self._toggle_btn.configure(
                text="⏹  Stop Overwatch",
                fg_color="#c92a2a",
                hover_color="#a51d1d",
            )
            self.set_status("Listening…")
            self.log("Overwatch activated ✅")
        else:
            self._toggle_btn.configure(
                text="▶  Start Overwatch",
                fg_color="#2b8a3e",
                hover_color="#237032",
            )
            self.set_status("Offline")
            self.log("Overwatch deactivated ⛔")

        if self._toggle_callback:
            self._toggle_callback(self._is_running)

    def _on_closing(self) -> None:
        """Graceful shutdown: signal threads, wait, then destroy."""
        logger.info("Shutdown requested — cleaning up …")
        self.log("Shutting down …")
        self.set_status("Shutting down…")

        # Signal all background threads to stop
        self.shutdown_event.set()

        # Wait for tracked threads (with a timeout so we never hang)
        for t in self._threads:
            t.join(timeout=3.0)
            if t.is_alive():
                logger.warning("Thread %s did not stop in time", t.name)

        logger.info("Shutdown complete")
        self.destroy()


if __name__ == "__main__":
    app = OverwatchGUI()
    app.mainloop()
