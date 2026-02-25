"""
PMC Overwatch GUI — stream-ready dark-mode interface with animated AI avatar.

The avatar uses a VTuber-inspired approach:
  • Clean high-res face displayed on transparent-matching dark background
  • Animated breathing glow aura that intensifies when speaking
  • Equalizer-style sound bars below chin for voice activity
  • Gentle floating/breathing bob animation for lifelike feel
  • All animation driven by state: idle → listening → thinking → speaking
"""

import logging
import math
import random
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

# ── Paths ────────────────────────────────────────────────────────────
_AVATAR_PATH = Path(__file__).parent / "assets" / "avatar.png"

# ── Palette ──────────────────────────────────────────────────────────
_BG = "#0d1117"
_CARD = "#161b22"
_SURFACE = "#1c2128"
_GREEN = "#2ea043"
_GREEN_H = "#238636"
_RED = "#da3633"
_RED_H = "#b62324"
_AMBER = "#d29922"
_CYAN = "#58a6ff"
_PURPLE = "#bc8cff"
_TEXT = "#e6edf3"
_TEXT2 = "#8b949e"
_MUTED = "#484f58"
_BORDER = "#30363d"

# Avatar
_AV_SIZE = 180          # Avatar image size
_CANVAS_W = 280         # Canvas width (room for glow)
_CANVAS_H = 260         # Canvas height (room for bars)
_EQ_BARS = 9            # Number of equalizer bars
_EQ_BAR_W = 6           # Bar width
_EQ_GAP = 3             # Bar gap
_FPS = 20


class OverwatchGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PMC Overwatch — Tarkov AI")
        self.geometry("760x700")
        self.minsize(560, 520)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=_BG)

        # State
        self._toggle_cb: Optional[Callable[[bool], None]] = None
        self._is_running = False
        self.shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []

        # Animation
        self._mode = "idle"
        self._phase = 0.0
        self._anim_id: Optional[str] = None
        self._pulse_id: Optional[str] = None
        self._pulse_vis = True
        self._dot_color = _MUTED

        # Equalizer bar heights (smooth targets + current)
        self._eq_target = [0.0] * _EQ_BARS
        self._eq_current = [0.0] * _EQ_BARS

        # Avatar image
        self._av_photo: Optional[ImageTk.PhotoImage] = None
        self._load_avatar()

        # Build
        self._build_header()
        self._build_avatar()
        self._build_log()
        self._build_footer()
        self._start_anim()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Load avatar ──────────────────────────────────────────────────
    def _load_avatar(self) -> None:
        if not _HAS_PIL or not _AVATAR_PATH.exists():
            return
        try:
            img = Image.open(_AVATAR_PATH).convert("RGBA")
            img = img.resize((_AV_SIZE, _AV_SIZE), Image.LANCZOS)
            # Circular mask with anti-aliased edge
            mask = Image.new("L", (_AV_SIZE, _AV_SIZE), 0)
            d = ImageDraw.Draw(mask)
            d.ellipse((0, 0, _AV_SIZE - 1, _AV_SIZE - 1), fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(1))
            img.putalpha(mask)
            # Composite onto app background colour
            bg = Image.new("RGBA", (_AV_SIZE, _AV_SIZE), (13, 17, 23, 255))
            bg.paste(img, (0, 0), img)
            self._av_photo = ImageTk.PhotoImage(bg.convert("RGB"))
        except Exception:
            logger.exception("Avatar load failed")

    # ══════════════════════════════════════════════════════════════════
    #  HEADER
    # ══════════════════════════════════════════════════════════════════
    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, corner_radius=12, fg_color=_CARD,
                           border_width=1, border_color=_BORDER)
        hdr.pack(fill="x", padx=16, pady=(14, 0))
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        logo = ctk.CTkFrame(inner, fg_color="transparent")
        logo.pack(side="left")
        ctk.CTkLabel(logo, text="⚔", font=ctk.CTkFont(size=28),
                     text_color=_GREEN).pack(side="left", padx=(0, 8))
        ttl = ctk.CTkFrame(logo, fg_color="transparent")
        ttl.pack(side="left")
        ctk.CTkLabel(ttl, text="PMC Overwatch",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=_TEXT).pack(anchor="w")
        ctk.CTkLabel(ttl, text="Tarkov AI Companion",
                     font=ctk.CTkFont(size=11),
                     text_color=_TEXT2).pack(anchor="w")

        self._btn = ctk.CTkButton(
            inner, text="▶  Start Overwatch", width=180, height=42,
            corner_radius=10, font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=_GREEN, hover_color=_GREEN_H, text_color="white",
            command=self._on_toggle)
        self._btn.pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  AVATAR — canvas with face, glow, EQ bars
    # ══════════════════════════════════════════════════════════════════
    def _build_avatar(self) -> None:
        # No card frame — avatar floats directly on the dark background
        self._cv = tk.Canvas(
            self, width=_CANVAS_W, height=_CANVAS_H,
            bg=_BG, highlightthickness=0, bd=0,
        )
        self._cv.pack(pady=(10, 0))

        # Status label under canvas
        self._av_status = ctk.CTkLabel(
            self, text="Offline",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_MUTED,
        )
        self._av_status.pack(pady=(0, 4))

    # ── Render one animation frame ────────────────────────────────────
    def _render_frame(self) -> None:
        cv = self._cv
        cv.delete("all")
        cx = _CANVAS_W // 2
        # Gentle vertical bob: avatar breathes up/down 2px
        bob = math.sin(self._phase * 0.6) * 2.0
        cy = (_CANVAS_H // 2) - 20 + bob
        r = _AV_SIZE // 2

        # ── Glow aura ────────────────────────────────────────────────
        if self._mode == "speaking":
            glow_color = _CYAN
            glow_alpha = 0.5 + math.sin(self._phase * 1.5) * 0.3
            glow_r = r + 12 + math.sin(self._phase * 2) * 4
        elif self._mode == "thinking":
            glow_color = _AMBER
            glow_alpha = 0.4 + math.sin(self._phase * 2) * 0.2
            glow_r = r + 10
        elif self._mode == "listening":
            glow_color = _GREEN
            glow_alpha = 0.3 + math.sin(self._phase * 0.8) * 0.1
            glow_r = r + 8
        else:
            glow_color = _MUTED
            glow_alpha = 0.2
            glow_r = r + 6

        # Draw concentric glow rings (fake transparency with brightness)
        for i in range(3):
            ring_r = glow_r + i * 4
            w = max(1, 3 - i)
            cv.create_oval(
                cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r,
                outline=glow_color, width=w,
            )

        # ── Avatar image ─────────────────────────────────────────────
        if self._av_photo is not None:
            cv.create_image(cx, cy, image=self._av_photo, anchor="center")
        else:
            cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                           fill=_SURFACE, outline=_BORDER, width=2)
            cv.create_text(cx, cy, text="🎮", font=("", 48))

        # ── Equalizer bars below avatar (voice activity indicator) ────
        eq_total_w = _EQ_BARS * _EQ_BAR_W + (_EQ_BARS - 1) * _EQ_GAP
        eq_x_start = cx - eq_total_w // 2
        eq_y_base = cy + r + 16  # Just below the avatar circle

        for i in range(_EQ_BARS):
            bh = max(2, int(self._eq_current[i] * 30))
            x = eq_x_start + i * (_EQ_BAR_W + _EQ_GAP)
            # Gradient colour per bar: centre bars are brightest
            center_factor = 1.0 - abs(i - _EQ_BARS // 2) / (_EQ_BARS // 2) * 0.5
            if self._mode == "speaking":
                bar_color = _CYAN
            elif self._mode == "thinking":
                bar_color = _AMBER
            elif self._mode == "listening":
                bar_color = _GREEN
            else:
                bar_color = _MUTED

            cv.create_rectangle(
                x, eq_y_base - bh, x + _EQ_BAR_W, eq_y_base,
                fill=bar_color, outline="",
            )

    # ── Animation loop ────────────────────────────────────────────────
    def _start_anim(self) -> None:
        self._tick()

    def _tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        self._phase += 0.15

        # Update EQ bar targets
        if self._mode == "speaking":
            for i in range(_EQ_BARS):
                center = 1.0 - abs(i - _EQ_BARS // 2) / (_EQ_BARS // 2) * 0.4
                self._eq_target[i] = max(0.15, (0.5 + random.uniform(-0.3, 0.3)) * center)
        elif self._mode == "thinking":
            for i in range(_EQ_BARS):
                self._eq_target[i] = (math.sin(self._phase * 2 + i * 0.5) + 1) * 0.2
        elif self._mode == "listening":
            for i in range(_EQ_BARS):
                self._eq_target[i] = 0.05 + (math.sin(self._phase * 0.5 + i * 0.3) + 1) * 0.05
        else:
            for i in range(_EQ_BARS):
                self._eq_target[i] = 0.0

        # Smooth interpolation
        for i in range(_EQ_BARS):
            self._eq_current[i] += (self._eq_target[i] - self._eq_current[i]) * 0.4

        self._render_frame()
        self._anim_id = self.after(1000 // _FPS, self._tick)

    # ══════════════════════════════════════════════════════════════════
    #  LOG
    # ══════════════════════════════════════════════════════════════════
    def _build_log(self) -> None:
        f = ctk.CTkFrame(self, corner_radius=12, fg_color=_CARD,
                         border_width=1, border_color=_BORDER)
        f.pack(fill="both", expand=True, padx=16, pady=8)
        ctk.CTkLabel(f, text="📋  Activity Log",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=_TEXT2).pack(anchor="w", padx=14, pady=(10, 0))
        self._log = ctk.CTkTextbox(
            f, font=ctk.CTkFont(family="Menlo", size=12),
            corner_radius=8, fg_color=_SURFACE,
            text_color=_TEXT, state="disabled", wrap="word", border_width=0)
        self._log.pack(fill="both", expand=True, padx=10, pady=(6, 10))

    # ══════════════════════════════════════════════════════════════════
    #  FOOTER
    # ══════════════════════════════════════════════════════════════════
    def _build_footer(self) -> None:
        ft = ctk.CTkFrame(self, corner_radius=12, fg_color=_CARD,
                          border_width=1, border_color=_BORDER, height=40)
        ft.pack(fill="x", padx=16, pady=(0, 14))
        ft.pack_propagate(False)
        inner = ctk.CTkFrame(ft, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=8)

        self._dot = ctk.CTkLabel(inner, text="●", font=ctk.CTkFont(size=12),
                                 text_color=_MUTED, width=16)
        self._dot.pack(side="left", padx=(0, 6))
        self._status_lbl = ctk.CTkLabel(inner, text="Offline",
                                        font=ctk.CTkFont(size=12), text_color=_TEXT2)
        self._status_lbl.pack(side="left")
        ctk.CTkLabel(inner, text="v3.0", font=ctk.CTkFont(size=11),
                     text_color=_MUTED).pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  PUBLIC API (thread-safe)
    # ══════════════════════════════════════════════════════════════════
    def set_toggle_callback(self, cb: Callable[[bool], None]) -> None:
        self._toggle_cb = cb

    def register_thread(self, t: threading.Thread) -> None:
        self._threads.append(t)

    def log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.after(0, self._do_log, f"[{ts}]  {msg}\n")

    def set_status(self, text: str) -> None:
        self.after(0, self._do_status, text)

    def force_toggle_off(self) -> None:
        self.after(0, self._force_off)

    def set_vis_mode(self, mode: str) -> None:
        self.after(0, self._set_mode, mode)

    # ══════════════════════════════════════════════════════════════════
    #  INTERNAL
    # ══════════════════════════════════════════════════════════════════
    def _do_log(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        labels = {
            "idle": ("Offline", _MUTED),
            "listening": ("🎧  Listening…", _GREEN),
            "speaking": ("🎙  Speaking…", _CYAN),
            "thinking": ("🧠  Thinking…", _AMBER),
        }
        t, c = labels.get(mode, ("Offline", _MUTED))
        self._av_status.configure(text=t, text_color=c)

    def _do_status(self, text: str) -> None:
        self._status_lbl.configure(text=text)
        lo = text.lower()
        if "listening" in lo:
            self._set_dot(_GREEN, True); self._set_mode("listening")
        elif "speaking" in lo:
            self._set_dot(_CYAN, True); self._set_mode("speaking")
        elif "thinking" in lo:
            self._set_dot(_AMBER, True); self._set_mode("thinking")
        elif "offline" in lo:
            self._set_dot(_MUTED, False); self._set_mode("idle")
        else:
            self._set_dot(_AMBER, False)

    def _set_dot(self, color: str, pulse: bool) -> None:
        self._dot_color = color
        self._dot.configure(text_color=color)
        if self._pulse_id:
            self.after_cancel(self._pulse_id)
            self._pulse_id = None
        if pulse:
            self._pulse_vis = True
            self._do_pulse()

    def _do_pulse(self) -> None:
        if self.shutdown_event.is_set():
            return
        self._pulse_vis = not self._pulse_vis
        self._dot.configure(text_color=self._dot_color if self._pulse_vis else _BG)
        self._pulse_id = self.after(600, self._do_pulse)

    def _force_off(self) -> None:
        self._is_running = False
        self._btn.configure(text="▶  Start Overwatch", fg_color=_GREEN,
                            hover_color=_GREEN_H)
        self._do_status("Offline")

    def _on_toggle(self) -> None:
        self._is_running = not self._is_running
        if self._is_running:
            self._btn.configure(text="⏹  Stop Overwatch", fg_color=_RED,
                                hover_color=_RED_H)
            self._do_status("Starting…")
            self.log("Overwatch activated ✅")
        else:
            self._btn.configure(text="▶  Start Overwatch", fg_color=_GREEN,
                                hover_color=_GREEN_H)
            self._do_status("Offline")
            self.log("Overwatch deactivated ⛔")
        if self._toggle_cb:
            self._toggle_cb(self._is_running)

    def _on_close(self) -> None:
        if self._anim_id:
            self.after_cancel(self._anim_id)
        if self._pulse_id:
            self.after_cancel(self._pulse_id)
        self.shutdown_event.set()
        for t in self._threads:
            t.join(timeout=3.0)
        self.destroy()


if __name__ == "__main__":
    app = OverwatchGUI()
    app.mainloop()
