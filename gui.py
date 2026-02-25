"""
PMC Overwatch GUI — premium dark-mode desktop interface.

Features:
  • Animated AI waveform visualizer (holographic effect when speaking)
  • Animated pulsing status dot (green/amber/grey)
  • Sleek dark theme with card-based layout
  • Thread-safe API for background updates
  • Graceful shutdown with thread coordination
"""

import logging
import math
import random
import threading
from datetime import datetime
from typing import Callable, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)

# ── Colour palette ───────────────────────────────────────────────────
_BG_DARK = "#0d1117"
_BG_CARD = "#161b22"
_BG_SURFACE = "#1c2128"
_ACCENT_GREEN = "#2ea043"
_ACCENT_GREEN_HOVER = "#238636"
_ACCENT_RED = "#da3633"
_ACCENT_RED_HOVER = "#b62324"
_ACCENT_AMBER = "#d29922"
_ACCENT_CYAN = "#58a6ff"
_ACCENT_PURPLE = "#bc8cff"
_TEXT_PRIMARY = "#e6edf3"
_TEXT_SECONDARY = "#8b949e"
_TEXT_MUTED = "#484f58"
_BORDER = "#30363d"

# Visualizer colours (holographic gradient)
_VIS_COLORS = ["#58a6ff", "#79c0ff", "#a5d6ff", "#bc8cff", "#d2a8ff"]
_VIS_IDLE_COLOR = "#21262d"
_VIS_BARS = 32
_VIS_HEIGHT = 80
_VIS_FPS = 24


class OverwatchGUI(ctk.CTk):
    """Main application window for PMC Overwatch."""

    def __init__(self) -> None:
        super().__init__()

        # ── Window setup ──────────────────────────────────────────────
        self.title("PMC Overwatch — Tarkov AI")
        self.geometry("820x720")
        self.minsize(600, 520)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=_BG_DARK)

        # ── State ─────────────────────────────────────────────────────
        self._toggle_callback: Optional[Callable[[bool], None]] = None
        self._is_running = False
        self.shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []

        # Status animation
        self._status_color = _TEXT_MUTED
        self._pulse_job: Optional[str] = None
        self._pulse_visible = True

        # Visualizer state
        self._vis_mode = "idle"  # idle, listening, speaking, thinking
        self._vis_job: Optional[str] = None
        self._vis_phase = 0.0
        self._vis_target_heights: list[float] = [0.0] * _VIS_BARS
        self._vis_current_heights: list[float] = [0.0] * _VIS_BARS

        # ── Build UI ──────────────────────────────────────────────────
        self._build_header()
        self._build_visualizer()
        self._build_log()
        self._build_footer()

        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ══════════════════════════════════════════════════════════════════
    #  HEADER
    # ══════════════════════════════════════════════════════════════════
    def _build_header(self) -> None:
        header = ctk.CTkFrame(
            self, corner_radius=12, fg_color=_BG_CARD,
            border_width=1, border_color=_BORDER,
        )
        header.pack(fill="x", padx=16, pady=(14, 0))

        inner = ctk.CTkFrame(header, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        logo = ctk.CTkFrame(inner, fg_color="transparent")
        logo.pack(side="left")

        ctk.CTkLabel(
            logo, text="⚔", font=ctk.CTkFont(size=28),
            text_color=_ACCENT_GREEN,
        ).pack(side="left", padx=(0, 8))

        titles = ctk.CTkFrame(logo, fg_color="transparent")
        titles.pack(side="left")

        ctk.CTkLabel(
            titles, text="PMC Overwatch",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=_TEXT_PRIMARY,
        ).pack(anchor="w")

        ctk.CTkLabel(
            titles, text="Tarkov AI Companion",
            font=ctk.CTkFont(size=11), text_color=_TEXT_SECONDARY,
        ).pack(anchor="w")

        self._toggle_btn = ctk.CTkButton(
            inner, text="▶  Start Overwatch", width=190, height=42,
            corner_radius=10, font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=_ACCENT_GREEN, hover_color=_ACCENT_GREEN_HOVER,
            text_color="white", command=self._on_toggle,
        )
        self._toggle_btn.pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  AI VISUALIZER — animated waveform/holographic bars
    # ══════════════════════════════════════════════════════════════════
    def _build_visualizer(self) -> None:
        vis_frame = ctk.CTkFrame(
            self, corner_radius=12, fg_color=_BG_CARD,
            border_width=1, border_color=_BORDER, height=_VIS_HEIGHT + 36,
        )
        vis_frame.pack(fill="x", padx=16, pady=(10, 0))
        vis_frame.pack_propagate(False)

        # Label
        label_frame = ctk.CTkFrame(vis_frame, fg_color="transparent")
        label_frame.pack(fill="x", padx=14, pady=(8, 0))

        self._vis_label = ctk.CTkLabel(
            label_frame, text="🤖  AI Status",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=_TEXT_SECONDARY,
        )
        self._vis_label.pack(side="left")

        self._vis_mode_label = ctk.CTkLabel(
            label_frame, text="Offline",
            font=ctk.CTkFont(size=11), text_color=_TEXT_MUTED,
        )
        self._vis_mode_label.pack(side="right")

        # Canvas for waveform
        self._vis_canvas = ctk.CTkCanvas(
            vis_frame, bg=_BG_CARD, highlightthickness=0,
            height=_VIS_HEIGHT,
        )
        self._vis_canvas.pack(fill="x", padx=12, pady=(4, 8))

        # Draw initial idle bars
        self._draw_visualizer()
        self._start_vis_animation()

    def _start_vis_animation(self) -> None:
        """Start the visualizer animation loop."""
        if self._vis_job is not None:
            self.after_cancel(self._vis_job)
        self._animate_vis()

    def _animate_vis(self) -> None:
        """One frame of the visualizer animation."""
        if self.shutdown_event.is_set():
            return

        self._vis_phase += 0.15

        # Generate target bar heights based on mode
        if self._vis_mode == "speaking":
            for i in range(_VIS_BARS):
                wave = math.sin(self._vis_phase + i * 0.4) * 0.3
                noise = random.uniform(-0.15, 0.15)
                center_bias = 1.0 - abs(i - _VIS_BARS / 2) / (_VIS_BARS / 2) * 0.4
                self._vis_target_heights[i] = max(0.1, (0.5 + wave + noise) * center_bias)
        elif self._vis_mode == "thinking":
            for i in range(_VIS_BARS):
                pulse = (math.sin(self._vis_phase * 2 + i * 0.3) + 1) * 0.25
                self._vis_target_heights[i] = pulse + 0.05
        elif self._vis_mode == "listening":
            for i in range(_VIS_BARS):
                gentle = (math.sin(self._vis_phase * 0.8 + i * 0.5) + 1) * 0.1
                self._vis_target_heights[i] = gentle + 0.03
        else:  # idle
            for i in range(_VIS_BARS):
                self._vis_target_heights[i] = 0.02

        # Smooth interpolation toward targets
        for i in range(_VIS_BARS):
            diff = self._vis_target_heights[i] - self._vis_current_heights[i]
            self._vis_current_heights[i] += diff * 0.3

        self._draw_visualizer()
        self._vis_job = self.after(1000 // _VIS_FPS, self._animate_vis)

    def _draw_visualizer(self) -> None:
        """Render the waveform bars on the canvas."""
        canvas = self._vis_canvas
        canvas.delete("all")

        w = canvas.winfo_width() or 400
        h = _VIS_HEIGHT
        bar_w = max(2, (w - _VIS_BARS * 2) // _VIS_BARS)
        gap = 2
        total_bar_w = bar_w + gap
        x_offset = (w - total_bar_w * _VIS_BARS) // 2

        for i in range(_VIS_BARS):
            bar_h = max(2, int(self._vis_current_heights[i] * h))
            x = x_offset + i * total_bar_w
            y_top = (h - bar_h) // 2
            y_bot = y_top + bar_h

            # Colour: gradient across bars
            if self._vis_mode == "idle":
                color = _VIS_IDLE_COLOR
            else:
                ci = i % len(_VIS_COLORS)
                color = _VIS_COLORS[ci]

            canvas.create_rectangle(
                x, y_top, x + bar_w, y_bot,
                fill=color, outline="", width=0,
            )

            # Glow line at top of each bar (holographic effect)
            if self._vis_mode != "idle" and bar_h > 4:
                glow_color = _VIS_COLORS[(i + 2) % len(_VIS_COLORS)]
                canvas.create_rectangle(
                    x, y_top, x + bar_w, y_top + 2,
                    fill=glow_color, outline="", width=0,
                )

    def set_vis_mode(self, mode: str) -> None:
        """Change visualizer mode. Thread-safe.

        Modes: 'idle', 'listening', 'speaking', 'thinking'
        """
        self.after(0, self._do_set_vis_mode, mode)

    def _do_set_vis_mode(self, mode: str) -> None:
        self._vis_mode = mode
        labels = {
            "idle": "Offline",
            "listening": "🎧 Listening…",
            "speaking": "🎙 Speaking…",
            "thinking": "🧠 Thinking…",
        }
        self._vis_mode_label.configure(
            text=labels.get(mode, mode),
            text_color=_ACCENT_CYAN if mode != "idle" else _TEXT_MUTED,
        )

    # ══════════════════════════════════════════════════════════════════
    #  LOG
    # ══════════════════════════════════════════════════════════════════
    def _build_log(self) -> None:
        log_frame = ctk.CTkFrame(
            self, corner_radius=12, fg_color=_BG_CARD,
            border_width=1, border_color=_BORDER,
        )
        log_frame.pack(fill="both", expand=True, padx=16, pady=10)

        log_header = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_header.pack(fill="x", padx=14, pady=(10, 0))

        ctk.CTkLabel(
            log_header, text="📋  Activity Log",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=_TEXT_SECONDARY,
        ).pack(side="left")

        self._log_box = ctk.CTkTextbox(
            log_frame, font=ctk.CTkFont(family="Menlo", size=12),
            corner_radius=8, fg_color=_BG_SURFACE,
            text_color=_TEXT_PRIMARY, state="disabled",
            wrap="word", border_width=0,
        )
        self._log_box.pack(fill="both", expand=True, padx=10, pady=(6, 10))

    # ══════════════════════════════════════════════════════════════════
    #  FOOTER
    # ══════════════════════════════════════════════════════════════════
    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(
            self, corner_radius=12, fg_color=_BG_CARD,
            border_width=1, border_color=_BORDER, height=44,
        )
        footer.pack(fill="x", padx=16, pady=(0, 14))
        footer.pack_propagate(False)

        inner = ctk.CTkFrame(footer, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=10)

        self._status_dot = ctk.CTkLabel(
            inner, text="●", font=ctk.CTkFont(size=12),
            text_color=_TEXT_MUTED, width=16,
        )
        self._status_dot.pack(side="left", padx=(0, 6))

        self._status_label = ctk.CTkLabel(
            inner, text="Offline",
            font=ctk.CTkFont(size=12), text_color=_TEXT_SECONDARY,
        )
        self._status_label.pack(side="left")

        ctk.CTkLabel(
            inner, text="v2.1",
            font=ctk.CTkFont(size=11), text_color=_TEXT_MUTED,
        ).pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  PUBLIC API (thread-safe)
    # ══════════════════════════════════════════════════════════════════
    def set_toggle_callback(self, cb: Callable[[bool], None]) -> None:
        self._toggle_callback = cb

    def register_thread(self, t: threading.Thread) -> None:
        self._threads.append(t)

    def log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.after(0, self._append_log, f"[{ts}]  {message}\n")

    def set_status(self, text: str) -> None:
        self.after(0, self._update_status, text)

    def force_toggle_off(self) -> None:
        self.after(0, self._do_force_toggle_off)

    # ══════════════════════════════════════════════════════════════════
    #  INTERNAL
    # ══════════════════════════════════════════════════════════════════
    def _append_log(self, line: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", line)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _update_status(self, text: str) -> None:
        self._status_label.configure(text=text)
        lower = text.lower()
        if "listening" in lower:
            self._set_dot_color(_ACCENT_GREEN, pulse=True)
            self.set_vis_mode("listening")
        elif "speaking" in lower:
            self._set_dot_color(_ACCENT_CYAN, pulse=True)
            self.set_vis_mode("speaking")
        elif "thinking" in lower:
            self._set_dot_color(_ACCENT_AMBER, pulse=True)
            self.set_vis_mode("thinking")
        elif "offline" in lower or "error" in lower:
            self._set_dot_color(_TEXT_MUTED, pulse=False)
            self.set_vis_mode("idle")
        else:
            self._set_dot_color(_ACCENT_AMBER, pulse=False)

    def _set_dot_color(self, color: str, pulse: bool = False) -> None:
        self._status_color = color
        self._status_dot.configure(text_color=color)
        if self._pulse_job is not None:
            self.after_cancel(self._pulse_job)
            self._pulse_job = None
        if pulse:
            self._pulse_visible = True
            self._pulse_tick()

    def _pulse_tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        self._pulse_visible = not self._pulse_visible
        self._status_dot.configure(
            text_color=self._status_color if self._pulse_visible else _BG_CARD
        )
        self._pulse_job = self.after(600, self._pulse_tick)

    def _do_force_toggle_off(self) -> None:
        self._is_running = False
        self._toggle_btn.configure(
            text="▶  Start Overwatch",
            fg_color=_ACCENT_GREEN, hover_color=_ACCENT_GREEN_HOVER,
        )
        self._update_status("Offline")

    def _on_toggle(self) -> None:
        self._is_running = not self._is_running
        if self._is_running:
            self._toggle_btn.configure(
                text="⏹  Stop Overwatch",
                fg_color=_ACCENT_RED, hover_color=_ACCENT_RED_HOVER,
            )
            self._update_status("Starting…")
            self.log("Overwatch activated ✅")
        else:
            self._toggle_btn.configure(
                text="▶  Start Overwatch",
                fg_color=_ACCENT_GREEN, hover_color=_ACCENT_GREEN_HOVER,
            )
            self._update_status("Offline")
            self.log("Overwatch deactivated ⛔")
        if self._toggle_callback:
            self._toggle_callback(self._is_running)

    def _on_closing(self) -> None:
        logger.info("Shutdown requested")
        self.log("Shutting down …")
        self._update_status("Shutting down…")
        if self._pulse_job:
            self.after_cancel(self._pulse_job)
        if self._vis_job:
            self.after_cancel(self._vis_job)
        self.shutdown_event.set()
        for t in self._threads:
            t.join(timeout=3.0)
            if t.is_alive():
                logger.warning("Thread %s did not stop", t.name)
        logger.info("Shutdown complete")
        self.destroy()


if __name__ == "__main__":
    app = OverwatchGUI()
    app.mainloop()
