"""
PMC Overwatch GUI — single avatar with immersive animated effects.

Design:
  • ONE consistent photorealistic avatar image
  • Floating particles orbit the avatar for liveliness
  • Gentle breathing bob + micro-sway for organic feel
  • 11 voice-reactive bars with smooth interpolation  
  • Multi-ring glow that pulses with state intensity
  • State-driven colour scheme for all effects
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
_TEXT = "#e6edf3"
_TEXT2 = "#8b949e"
_MUTED = "#484f58"
_BORDER = "#30363d"

# Avatar sizing
_AV_SIZE = 200
_CANVAS_W = 300
_CANVAS_H = 310
_FPS = 20

# Voice bars
_N_BARS = 13
_BAR_W = 4
_BAR_GAP = 3
_BAR_MAX_H = 24

# Glow per state
_GLOW = {"idle": _MUTED, "listening": _GREEN, "thinking": _AMBER, "speaking": _CYAN}

# Particles
_N_PARTICLES = 16


class _Particle:
    __slots__ = ("angle", "radius", "speed", "size", "alpha")

    def __init__(self):
        self.angle = random.uniform(0, 2 * math.pi)
        self.radius = random.uniform(110, 150)
        self.speed = random.uniform(0.003, 0.012)
        self.size = random.uniform(1.5, 3.5)
        self.alpha = random.uniform(0.3, 0.8)


class OverwatchGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PMC Overwatch — Tarkov AI")
        self.geometry("780x760")
        self.minsize(580, 560)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=_BG)

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

        # Voice bar levels
        self._bar_target = [0.0] * _N_BARS
        self._bar_current = [0.0] * _N_BARS

        # Particles
        self._particles = [_Particle() for _ in range(_N_PARTICLES)]

        # Load avatar
        self._av_photo: Optional[ImageTk.PhotoImage] = None
        self._load_avatar()

        self._build_header()
        self._build_avatar()
        self._build_log()
        self._build_footer()
        self._start_anim()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_avatar(self) -> None:
        if not _HAS_PIL or not _AVATAR_PATH.exists():
            return
        try:
            img = Image.open(_AVATAR_PATH).convert("RGBA")
            img = img.resize((_AV_SIZE, _AV_SIZE), Image.LANCZOS)
            mask = Image.new("L", (_AV_SIZE, _AV_SIZE), 0)
            d = ImageDraw.Draw(mask)
            d.ellipse((0, 0, _AV_SIZE - 1, _AV_SIZE - 1), fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(1))
            img.putalpha(mask)
            bg = Image.new("RGBA", (_AV_SIZE, _AV_SIZE), (13, 17, 23, 255))
            bg.paste(img, (0, 0), img)
            self._av_photo = ImageTk.PhotoImage(bg.convert("RGB"))
        except Exception:
            logger.exception("Avatar load failed")

    # ══════════════════════════════════════════════════════════════════
    #  HEADER
    # ══════════════════════════════════════════════════════════════════
    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, corner_radius=14, fg_color=_CARD,
                           border_width=1, border_color=_BORDER)
        hdr.pack(fill="x", padx=18, pady=(16, 0))
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=14)

        logo = ctk.CTkFrame(inner, fg_color="transparent")
        logo.pack(side="left")
        ctk.CTkLabel(logo, text="⚔", font=ctk.CTkFont(size=30),
                     text_color=_GREEN).pack(side="left", padx=(0, 10))
        ttl = ctk.CTkFrame(logo, fg_color="transparent")
        ttl.pack(side="left")
        ctk.CTkLabel(ttl, text="PMC Overwatch",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=_TEXT).pack(anchor="w")
        ctk.CTkLabel(ttl, text="AI Companion • Escape from Tarkov",
                     font=ctk.CTkFont(size=11), text_color=_TEXT2).pack(anchor="w")

        self._btn = ctk.CTkButton(
            inner, text="▶  Start", width=140, height=44,
            corner_radius=12, font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=_GREEN, hover_color=_GREEN_H, text_color="white",
            command=self._on_toggle)
        self._btn.pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  AVATAR — effects-driven animation
    # ══════════════════════════════════════════════════════════════════
    def _build_avatar(self) -> None:
        self._cv = tk.Canvas(self, width=_CANVAS_W, height=_CANVAS_H,
                             bg=_BG, highlightthickness=0, bd=0)
        self._cv.pack(pady=(10, 0))

        self._av_status = ctk.CTkLabel(
            self, text="Offline",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=_MUTED)
        self._av_status.pack(pady=(2, 4))

    def _render(self) -> None:
        cv = self._cv
        cv.delete("all")
        cx = _CANVAS_W // 2
        # Breathing bob + micro-sway
        bob = math.sin(self._phase * 0.35) * 2.5
        sway = math.sin(self._phase * 0.18) * 1.5
        cy = _CANVAS_H // 2 - 20 + bob
        r = _AV_SIZE // 2
        glow_c = _GLOW.get(self._mode, _MUTED)

        # ── Particles (orbit the avatar) ──────────────────────────────
        for p in self._particles:
            p.angle += p.speed
            px = cx + sway + math.cos(p.angle) * p.radius
            py = cy + math.sin(p.angle) * p.radius * 0.85
            sz = p.size
            if self._mode == "speaking":
                sz *= 1.0 + (math.sin(self._phase * 2 + p.angle) + 1) * 0.4
            cv.create_oval(px - sz, py - sz, px + sz, py + sz,
                           fill=glow_c, outline="")

        # ── Glow rings ────────────────────────────────────────────────
        pulse = (math.sin(self._phase * 1.2) + 1) * 0.5
        # Outer halo
        outer_r = r + 16 + pulse * 4
        cv.create_oval(cx + sway - outer_r, cy - outer_r,
                       cx + sway + outer_r, cy + outer_r,
                       outline=glow_c, width=1)
        # Middle ring
        mid_r = r + 10 + pulse * 2
        ring_w = 3 if self._mode != "idle" else 2
        cv.create_oval(cx + sway - mid_r, cy - mid_r,
                       cx + sway + mid_r, cy + mid_r,
                       outline=glow_c, width=ring_w)

        # ── Avatar image ──────────────────────────────────────────────
        if self._av_photo is not None:
            cv.create_image(cx + sway, cy, image=self._av_photo, anchor="center")
        else:
            cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                           fill=_SURFACE, outline=_BORDER, width=2)
            cv.create_text(cx, cy, text="🎮", font=("", 48))

        # ── Voice bars (below avatar) ─────────────────────────────────
        total_w = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        bx_start = cx - total_w // 2
        by_base = cy + r + 28

        for i in range(_N_BARS):
            h = max(2, int(self._bar_current[i] * _BAR_MAX_H))
            x = bx_start + i * (_BAR_W + _BAR_GAP)
            # Main bar
            cv.create_rectangle(x, by_base - h, x + _BAR_W, by_base,
                                fill=glow_c, outline="")
            # Reflection
            ref_h = max(1, h // 4)
            cv.create_rectangle(x, by_base + 2, x + _BAR_W, by_base + 2 + ref_h,
                                fill=_BORDER, outline="")

    # ── Animation loop ────────────────────────────────────────────────
    def _start_anim(self) -> None:
        self._tick()

    def _tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        self._phase += 0.12

        # Update bar targets
        if self._mode == "speaking":
            for i in range(_N_BARS):
                center_w = 1.0 - abs(i - _N_BARS // 2) / (_N_BARS // 2) * 0.3
                self._bar_target[i] = max(0.15, random.uniform(0.25, 1.0) * center_w)
        elif self._mode == "thinking":
            for i in range(_N_BARS):
                wave = (math.sin(self._phase * 2.0 + i * 0.5) + 1) * 0.5
                self._bar_target[i] = wave * 0.25
        elif self._mode == "listening":
            for i in range(_N_BARS):
                self._bar_target[i] = 0.05 + (math.sin(self._phase * 0.6 + i * 0.3) + 1) * 0.03
        else:
            for i in range(_N_BARS):
                self._bar_target[i] = 0.0

        # Smooth interpolation
        for i in range(_N_BARS):
            diff = self._bar_target[i] - self._bar_current[i]
            self._bar_current[i] += diff * 0.35

        self._render()
        self._anim_id = self.after(1000 // _FPS, self._tick)

    # ══════════════════════════════════════════════════════════════════
    #  LOG
    # ══════════════════════════════════════════════════════════════════
    def _build_log(self) -> None:
        f = ctk.CTkFrame(self, corner_radius=14, fg_color=_CARD,
                         border_width=1, border_color=_BORDER)
        f.pack(fill="both", expand=True, padx=18, pady=10)
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(hdr, text="📋  Activity Log",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=_TEXT2).pack(side="left")
        self._log = ctk.CTkTextbox(
            f, font=ctk.CTkFont(family="Menlo", size=12),
            corner_radius=10, fg_color=_SURFACE,
            text_color=_TEXT, state="disabled", wrap="word", border_width=0)
        self._log.pack(fill="both", expand=True, padx=12, pady=(6, 12))

    # ══════════════════════════════════════════════════════════════════
    #  FOOTER
    # ══════════════════════════════════════════════════════════════════
    def _build_footer(self) -> None:
        ft = ctk.CTkFrame(self, corner_radius=14, fg_color=_CARD,
                          border_width=1, border_color=_BORDER, height=42)
        ft.pack(fill="x", padx=18, pady=(0, 16))
        ft.pack_propagate(False)
        inner = ctk.CTkFrame(ft, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=10)
        self._dot = ctk.CTkLabel(inner, text="●", font=ctk.CTkFont(size=12),
                                 text_color=_MUTED, width=16)
        self._dot.pack(side="left", padx=(0, 6))
        self._status_lbl = ctk.CTkLabel(inner, text="Offline",
                                        font=ctk.CTkFont(size=12), text_color=_TEXT2)
        self._status_lbl.pack(side="left")
        ctk.CTkLabel(inner, text="v5.0", font=ctk.CTkFont(size=11),
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
        self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H)
        self._do_status("Offline")

    def _on_toggle(self) -> None:
        self._is_running = not self._is_running
        if self._is_running:
            self._btn.configure(text="⏹  Stop", fg_color=_RED, hover_color=_RED_H)
            self._do_status("Starting…")
            self.log("Overwatch activated ✅")
        else:
            self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H)
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
