"""
PMC Overwatch GUI — premium dark-mode desktop interface with animated avatar.

Features:
  • Animated AI avatar with glow ring effects (reacts to state)
  • Animated waveform visualizer (holographic bars)
  • Pulsing status dot
  • Sleek dark theme with card-based layout
  • Thread-safe API for background updates
"""

import logging
import math
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)

# Try to import PIL for avatar
try:
    from PIL import Image, ImageDraw, ImageFilter
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False
    logger.warning("Pillow not found — avatar will be text-only")

# ── Paths ────────────────────────────────────────────────────────────
_AVATAR_PATH = Path(__file__).parent / "assets" / "avatar.png"

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
_ACCENT_PINK = "#f778ba"
_TEXT_PRIMARY = "#e6edf3"
_TEXT_SECONDARY = "#8b949e"
_TEXT_MUTED = "#484f58"
_BORDER = "#30363d"

# Visualizer
_VIS_COLORS = ["#58a6ff", "#79c0ff", "#a5d6ff", "#bc8cff", "#d2a8ff"]
_VIS_IDLE_COLOR = "#21262d"
_VIS_BARS = 32
_VIS_HEIGHT = 60
_VIS_FPS = 24

# Avatar
_AVATAR_SIZE = 120
_RING_COLORS = {
    "idle": "#30363d",
    "listening": "#2ea043",
    "speaking": "#58a6ff",
    "thinking": "#d29922",
}


class OverwatchGUI(ctk.CTk):
    """Main application window for PMC Overwatch."""

    def __init__(self) -> None:
        super().__init__()

        self.title("PMC Overwatch — Tarkov AI")
        self.geometry("820x780")
        self.minsize(600, 580)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=_BG_DARK)

        # ── State ─────────────────────────────────────────────────────
        self._toggle_callback: Optional[Callable[[bool], None]] = None
        self._is_running = False
        self.shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []

        # Animation state
        self._status_color = _TEXT_MUTED
        self._pulse_job: Optional[str] = None
        self._pulse_visible = True
        self._vis_mode = "idle"
        self._vis_job: Optional[str] = None
        self._vis_phase = 0.0
        self._vis_target_h: list[float] = [0.0] * _VIS_BARS
        self._vis_current_h: list[float] = [0.0] * _VIS_BARS

        # Avatar glow state
        self._avatar_glow_phase = 0.0
        self._avatar_ring_color = _RING_COLORS["idle"]
        self._avatar_job: Optional[str] = None

        # ── Build UI ──────────────────────────────────────────────────
        self._build_header()
        self._build_avatar_section()
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
    #  AVATAR + VISUALIZER section
    # ══════════════════════════════════════════════════════════════════
    def _build_avatar_section(self) -> None:
        """Build the avatar with animated glow ring + waveform bars."""
        section = ctk.CTkFrame(
            self, corner_radius=12, fg_color=_BG_CARD,
            border_width=1, border_color=_BORDER,
        )
        section.pack(fill="x", padx=16, pady=(10, 0))

        # Top row: avatar + info
        top = ctk.CTkFrame(section, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 4))

        # ── Avatar with glow ring ─────────────────────────────────────
        avatar_frame = ctk.CTkFrame(top, fg_color="transparent")
        avatar_frame.pack(side="left")

        ring_size = _AVATAR_SIZE + 16
        self._avatar_canvas = ctk.CTkCanvas(
            avatar_frame, width=ring_size, height=ring_size,
            bg=_BG_CARD, highlightthickness=0,
        )
        self._avatar_canvas.pack()

        # Load avatar image
        self._avatar_photo = None
        if _HAS_PIL and _AVATAR_PATH.exists():
            try:
                img = Image.open(_AVATAR_PATH).convert("RGBA")
                img = img.resize((_AVATAR_SIZE, _AVATAR_SIZE), Image.LANCZOS)

                # Create circular mask
                mask = Image.new("L", (_AVATAR_SIZE, _AVATAR_SIZE), 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((0, 0, _AVATAR_SIZE - 1, _AVATAR_SIZE - 1), fill=255)
                img.putalpha(mask)

                # Composite onto dark background for Tkinter
                bg = Image.new("RGBA", (_AVATAR_SIZE, _AVATAR_SIZE), (13, 17, 23, 255))
                bg.paste(img, (0, 0), img)
                final = bg.convert("RGB")

                import tkinter as tk
                from PIL import ImageTk
                self._avatar_photo = ImageTk.PhotoImage(final)
            except Exception:
                logger.exception("Failed to load avatar image")

        self._draw_avatar_ring()
        self._start_avatar_animation()

        # ── Info panel next to avatar ─────────────────────────────────
        info = ctk.CTkFrame(top, fg_color="transparent")
        info.pack(side="left", padx=(16, 0), fill="both", expand=True)

        self._avatar_name_label = ctk.CTkLabel(
            info, text="AI Companion",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=_TEXT_PRIMARY,
        )
        self._avatar_name_label.pack(anchor="w")

        self._avatar_status_label = ctk.CTkLabel(
            info, text="Offline",
            font=ctk.CTkFont(size=12), text_color=_TEXT_MUTED,
        )
        self._avatar_status_label.pack(anchor="w", pady=(2, 0))

        # Mouth/speaking indicator
        self._mouth_frame = ctk.CTkFrame(info, fg_color="transparent")
        self._mouth_frame.pack(anchor="w", pady=(8, 0))

        self._mouth_bars: list[ctk.CTkFrame] = []
        for i in range(5):
            bar = ctk.CTkFrame(
                self._mouth_frame, width=4, height=4,
                corner_radius=2, fg_color=_TEXT_MUTED,
            )
            bar.pack(side="left", padx=1)
            self._mouth_bars.append(bar)

        # ── Waveform visualizer ───────────────────────────────────────
        self._vis_canvas = ctk.CTkCanvas(
            section, height=_VIS_HEIGHT, bg=_BG_CARD, highlightthickness=0,
        )
        self._vis_canvas.pack(fill="x", padx=12, pady=(4, 10))

        self._draw_visualizer()
        self._start_vis_animation()

    # ── Avatar ring drawing + animation ───────────────────────────────
    def _draw_avatar_ring(self) -> None:
        """Draw the avatar with a coloured glow ring."""
        c = self._avatar_canvas
        c.delete("all")
        ring_size = _AVATAR_SIZE + 16
        cx, cy = ring_size // 2, ring_size // 2
        r = _AVATAR_SIZE // 2

        # Outer glow ring
        glow_r = r + 6
        pulse = (math.sin(self._avatar_glow_phase) + 1) * 0.5  # 0–1
        alpha_factor = 0.5 + pulse * 0.5

        # Draw ring as arc segments for colour variation
        color = self._avatar_ring_color
        ring_width = 3
        c.create_oval(
            cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
            outline=color, width=ring_width,
        )

        # Inner subtle ring
        inner_r = r + 2
        c.create_oval(
            cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r,
            outline=color, width=1,
        )

        # Avatar image or placeholder
        if self._avatar_photo is not None:
            c.create_image(cx, cy, image=self._avatar_photo, anchor="center")
        else:
            # Placeholder circle
            c.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=_BG_SURFACE, outline=_BORDER, width=2,
            )
            c.create_text(
                cx, cy, text="🎮", font=("", 36),
                fill=_TEXT_PRIMARY,
            )

    def _start_avatar_animation(self) -> None:
        if self._avatar_job is not None:
            self.after_cancel(self._avatar_job)
        self._animate_avatar()

    def _animate_avatar(self) -> None:
        if self.shutdown_event.is_set():
            return
        self._avatar_glow_phase += 0.12

        # Animate mouth bars when speaking
        if self._vis_mode == "speaking":
            for i, bar in enumerate(self._mouth_bars):
                h = random.randint(4, 18)
                bar.configure(height=h, fg_color=_ACCENT_CYAN)
        elif self._vis_mode == "thinking":
            phase = self._avatar_glow_phase
            for i, bar in enumerate(self._mouth_bars):
                h = int(4 + 6 * (math.sin(phase + i * 0.5) + 1))
                bar.configure(height=h, fg_color=_ACCENT_AMBER)
        elif self._vis_mode == "listening":
            for i, bar in enumerate(self._mouth_bars):
                h = int(4 + 3 * (math.sin(self._avatar_glow_phase * 0.5 + i) + 1))
                bar.configure(height=h, fg_color=_ACCENT_GREEN)
        else:
            for bar in self._mouth_bars:
                bar.configure(height=4, fg_color=_TEXT_MUTED)

        self._draw_avatar_ring()
        self._avatar_job = self.after(1000 // 16, self._animate_avatar)

    # ── Waveform animation ────────────────────────────────────────────
    def _start_vis_animation(self) -> None:
        if self._vis_job is not None:
            self.after_cancel(self._vis_job)
        self._animate_vis()

    def _animate_vis(self) -> None:
        if self.shutdown_event.is_set():
            return
        self._vis_phase += 0.15

        if self._vis_mode == "speaking":
            for i in range(_VIS_BARS):
                wave = math.sin(self._vis_phase + i * 0.4) * 0.3
                noise = random.uniform(-0.15, 0.15)
                center = 1.0 - abs(i - _VIS_BARS / 2) / (_VIS_BARS / 2) * 0.4
                self._vis_target_h[i] = max(0.1, (0.5 + wave + noise) * center)
        elif self._vis_mode == "thinking":
            for i in range(_VIS_BARS):
                pulse = (math.sin(self._vis_phase * 2 + i * 0.3) + 1) * 0.25
                self._vis_target_h[i] = pulse + 0.05
        elif self._vis_mode == "listening":
            for i in range(_VIS_BARS):
                gentle = (math.sin(self._vis_phase * 0.8 + i * 0.5) + 1) * 0.1
                self._vis_target_h[i] = gentle + 0.03
        else:
            for i in range(_VIS_BARS):
                self._vis_target_h[i] = 0.02

        for i in range(_VIS_BARS):
            self._vis_current_h[i] += (self._vis_target_h[i] - self._vis_current_h[i]) * 0.3

        self._draw_visualizer()
        self._vis_job = self.after(1000 // _VIS_FPS, self._animate_vis)

    def _draw_visualizer(self) -> None:
        canvas = self._vis_canvas
        canvas.delete("all")
        w = canvas.winfo_width() or 400
        h = _VIS_HEIGHT
        bar_w = max(2, (w - _VIS_BARS * 2) // _VIS_BARS)
        gap = 2
        total = bar_w + gap
        x_off = (w - total * _VIS_BARS) // 2

        for i in range(_VIS_BARS):
            bh = max(2, int(self._vis_current_h[i] * h))
            x = x_off + i * total
            yt = (h - bh) // 2
            yb = yt + bh
            color = _VIS_IDLE_COLOR if self._vis_mode == "idle" else _VIS_COLORS[i % len(_VIS_COLORS)]
            canvas.create_rectangle(x, yt, x + bar_w, yb, fill=color, outline="")
            if self._vis_mode != "idle" and bh > 4:
                canvas.create_rectangle(x, yt, x + bar_w, yt + 2,
                    fill=_VIS_COLORS[(i + 2) % len(_VIS_COLORS)], outline="")

    # ══════════════════════════════════════════════════════════════════
    #  LOG
    # ══════════════════════════════════════════════════════════════════
    def _build_log(self) -> None:
        log_frame = ctk.CTkFrame(
            self, corner_radius=12, fg_color=_BG_CARD,
            border_width=1, border_color=_BORDER,
        )
        log_frame.pack(fill="both", expand=True, padx=16, pady=10)

        hdr = ctk.CTkFrame(log_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(
            hdr, text="📋  Activity Log",
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
            inner, text="Offline", font=ctk.CTkFont(size=12),
            text_color=_TEXT_SECONDARY,
        )
        self._status_label.pack(side="left")

        ctk.CTkLabel(
            inner, text="v2.2", font=ctk.CTkFont(size=11),
            text_color=_TEXT_MUTED,
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

    def set_vis_mode(self, mode: str) -> None:
        self.after(0, self._do_set_vis_mode, mode)

    # ══════════════════════════════════════════════════════════════════
    #  INTERNAL
    # ══════════════════════════════════════════════════════════════════
    def _append_log(self, line: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", line)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _do_set_vis_mode(self, mode: str) -> None:
        self._vis_mode = mode
        self._avatar_ring_color = _RING_COLORS.get(mode, _RING_COLORS["idle"])

        status_map = {
            "idle": ("Offline", _TEXT_MUTED),
            "listening": ("🎧 Listening…", _ACCENT_GREEN),
            "speaking": ("🎙 Speaking…", _ACCENT_CYAN),
            "thinking": ("🧠 Thinking…", _ACCENT_AMBER),
        }
        text, color = status_map.get(mode, ("Offline", _TEXT_MUTED))
        self._avatar_status_label.configure(text=text, text_color=color)

    def _update_status(self, text: str) -> None:
        self._status_label.configure(text=text)
        lower = text.lower()
        if "listening" in lower:
            self._set_dot_color(_ACCENT_GREEN, pulse=True)
            self._do_set_vis_mode("listening")
        elif "speaking" in lower:
            self._set_dot_color(_ACCENT_CYAN, pulse=True)
            self._do_set_vis_mode("speaking")
        elif "thinking" in lower:
            self._set_dot_color(_ACCENT_AMBER, pulse=True)
            self._do_set_vis_mode("thinking")
        elif "offline" in lower or "error" in lower:
            self._set_dot_color(_TEXT_MUTED, pulse=False)
            self._do_set_vis_mode("idle")
        else:
            self._set_dot_color(_ACCENT_AMBER, pulse=False)

    def _set_dot_color(self, color: str, pulse: bool = False) -> None:
        self._status_color = color
        self._status_dot.configure(text_color=color)
        if self._pulse_job:
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
        if self._avatar_job:
            self.after_cancel(self._avatar_job)
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
