"""
PMC Overwatch GUI — live animated avatar with state-reactive effects.

Design:
  * Loads avatar image and creates pre-rendered overlay frames
  * Animated effects: eye blink, speaking glow, aura pulse, breathing
  * Dynamic voice waveform bars
  * State-driven colour scheme for all effects
  * Smooth 24fps animation loop
"""

import logging
import math
import platform
import random
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

# ── Avatar asset path ────────────────────────────────────────────────
_AVATAR_PATH = Path(__file__).parent / "assets" / "avatar.png"

# ── Palette ──────────────────────────────────────────────────────────
_BG = "#0a0e14"
_BG_RGB = (10, 14, 20)
_CARD = "#12171f"
_SURFACE = "#1a1f28"
_GREEN = "#00d26a"
_GREEN_H = "#00b85c"
_RED = "#ff4757"
_RED_H = "#e8404f"
_AMBER = "#ffa502"
_CYAN = "#00d2ff"
_TEXT = "#e6edf3"
_TEXT2 = "#7b8794"
_MUTED = "#3d4450"
_BORDER = "#252b35"

# Agent sizing
_AV_SIZE = 220
_CANVAS_W = 360
_CANVAS_H = 360
_FPS = 24

# Voice bars
_N_BARS = 29
_BAR_W = 3
_BAR_GAP = 1
_BAR_MAX_H = 16

# Glow per state
_GLOW = {
    "idle": _MUTED,
    "listening": _GREEN,
    "thinking": _AMBER,
    "speaking": _CYAN,
}
_GLOW_RGB = {
    "idle": (61, 68, 80),
    "listening": (0, 210, 106),
    "thinking": (255, 165, 2),
    "speaking": (0, 210, 255),
}

# Particles
_N_PARTICLES = 16


class _Particle:
    __slots__ = ("angle", "radius", "speed", "size")

    def __init__(self):
        self.angle = random.uniform(0, 2 * math.pi)
        self.radius = random.uniform(118, 148)
        self.speed = random.uniform(0.004, 0.012)
        self.size = random.uniform(1.0, 2.4)


def _prepare_avatar_frames():
    """Pre-render avatar frames: normal, blink, speak, and dimmed versions."""
    if not _HAS_PIL or not _AVATAR_PATH.exists():
        return {}

    try:
        raw = Image.open(_AVATAR_PATH).convert("RGBA")
        raw = raw.resize((_AV_SIZE, _AV_SIZE), Image.Resampling.LANCZOS)

        # Circular mask
        mask = Image.new("L", (_AV_SIZE, _AV_SIZE), 0)
        d = ImageDraw.Draw(mask)
        d.ellipse([3, 3, _AV_SIZE - 4, _AV_SIZE - 4], fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(1.5))

        def _composite(img):
            img.putalpha(mask)
            bg = Image.new("RGBA", (_AV_SIZE, _AV_SIZE), (*_BG_RGB, 255))
            bg.paste(img, (0, 0), img)
            return ImageTk.PhotoImage(bg.convert("RGB"))

        frames = {}

        # Normal frame
        frames["normal"] = _composite(raw.copy())

        # Blink frame: draw dark eyelid strips over the eyes
        blink = raw.copy()
        bd = ImageDraw.Draw(blink)
        # Approximate eye regions (center-ish of 220px image)
        cx, cy = _AV_SIZE // 2, _AV_SIZE // 2 - 12
        for side in [-1, 1]:
            ex = cx + side * 28
            bd.ellipse([ex - 16, cy - 3, ex + 16, cy + 5],
                       fill=(120, 85, 70, 200))
        frames["blink"] = _composite(blink)

        # Speaking frame: slightly brighter + subtle mouth glow
        speak = raw.copy()
        enhancer = ImageEnhance.Brightness(speak)
        speak = enhancer.enhance(1.08)
        speak = speak.convert("RGBA")
        # Add subtle cyan glow near mouth area
        glow_overlay = Image.new("RGBA", (_AV_SIZE, _AV_SIZE), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_overlay)
        mouth_y = cy + 38
        gd.ellipse([cx - 20, mouth_y - 8, cx + 20, mouth_y + 8],
                    fill=(0, 200, 255, 30))
        speak = Image.alpha_composite(speak, glow_overlay)
        frames["speak"] = _composite(speak)

        # Thinking frame: slightly yellow tinted
        think = raw.copy()
        think_overlay = Image.new("RGBA", (_AV_SIZE, _AV_SIZE), (255, 165, 0, 12))
        think = Image.alpha_composite(think, think_overlay)
        frames["think"] = _composite(think)

        # Listening frame: slightly green tinted
        listen = raw.copy()
        listen_overlay = Image.new("RGBA", (_AV_SIZE, _AV_SIZE), (0, 210, 100, 10))
        listen = Image.alpha_composite(listen, listen_overlay)
        frames["listen"] = _composite(listen)

        logger.info("Avatar frames pre-rendered (%d frames)", len(frames))
        return frames

    except Exception:
        logger.exception("Avatar frame prep failed")
        return {}


class OverwatchGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PMC Overwatch")
        self.geometry("860x880")
        self.minsize(640, 660)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=_BG)

        self._toggle_cb: Optional[Callable[[bool], None]] = None
        self._is_running = False
        self.shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []

        # Animation state
        self._mode = "idle"
        self._phase = 0.0
        self._anim_id: Optional[str] = None
        self._pulse_id: Optional[str] = None
        self._pulse_vis = True
        self._dot_color = _MUTED
        self._blink_timer = 0.0
        self._blink_active = False
        self._speak_glow = 0.0

        # Voice bar levels
        self._bar_target = [0.0] * _N_BARS
        self._bar_current = [0.0] * _N_BARS

        # Particles
        self._particles = [_Particle() for _ in range(_N_PARTICLES)]

        # Pre-render avatar frames
        self._frames = _prepare_avatar_frames()

        self._build_header()
        self._build_agent()
        self._build_log()
        self._build_footer()
        self._start_anim()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ══════════════════════════════════════════════════════════════════
    #  HEADER
    # ══════════════════════════════════════════════════════════════════
    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, corner_radius=16, fg_color=_CARD,
                           border_width=1, border_color=_BORDER)
        hdr.pack(fill="x", padx=20, pady=(16, 0))
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=12)

        logo = ctk.CTkFrame(inner, fg_color="transparent")
        logo.pack(side="left")
        _font = "Segoe UI" if platform.system() == "Windows" else "SF Pro Display"
        ctk.CTkLabel(logo, text="PMC Overwatch",
                     font=ctk.CTkFont(family=_font, size=22, weight="bold"),
                     text_color=_TEXT).pack(anchor="w")
        ctk.CTkLabel(logo, text="AI Companion  ·  Escape from Tarkov",
                     font=ctk.CTkFont(size=11), text_color=_TEXT2).pack(anchor="w")

        self._btn = ctk.CTkButton(
            inner, text="▶  Start", width=140, height=42,
            corner_radius=12, font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=_GREEN, hover_color=_GREEN_H, text_color="white",
            command=self._on_toggle)
        self._btn.pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  AGENT CANVAS — Live animated avatar
    # ══════════════════════════════════════════════════════════════════
    def _build_agent(self) -> None:
        self._cv = tk.Canvas(self, width=_CANVAS_W, height=_CANVAS_H,
                             bg=_BG, highlightthickness=0, bd=0)
        self._cv.pack(pady=(4, 0))

        self._av_status = ctk.CTkLabel(
            self, text="OFFLINE",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=_MUTED)
        self._av_status.pack(pady=(0, 4))

    def _render(self) -> None:
        cv = self._cv
        cv.delete("all")
        cx = _CANVAS_W // 2
        cy = _CANVAS_H // 2 - 10

        # Breathing motion
        breath = math.sin(self._phase * 0.3) * 3.0
        sway = math.sin(self._phase * 0.15) * 1.5
        cy_f = cy + breath
        glow_c = _GLOW.get(self._mode, _MUTED)
        glow_rgb = _GLOW_RGB.get(self._mode, (61, 68, 80))

        # ── Background aura (soft radial glow) ────────────────────────
        if self._mode != "idle":
            pulse = (math.sin(self._phase * 0.6) + 1) * 0.5
            for i in range(4):
                r = _AV_SIZE // 2 + 20 + i * 12 + pulse * 8
                alpha = max(0, 60 - i * 18)
                fade_r = int(glow_rgb[0] * alpha / 255)
                fade_g = int(glow_rgb[1] * alpha / 255)
                fade_b = int(glow_rgb[2] * alpha / 255)
                c = f"#{max(10, fade_r):02x}{max(14, fade_g):02x}{max(20, fade_b):02x}"
                cv.create_oval(cx + sway - r, cy_f - r,
                               cx + sway + r, cy_f + r,
                               outline=c, width=2)

        # ── Particles ─────────────────────────────────────────────────
        for p in self._particles:
            p.angle += p.speed
            r = p.radius + math.sin(self._phase * 0.4 + p.angle * 2) * 6
            px = cx + sway + math.cos(p.angle) * r
            py = cy_f + math.sin(p.angle) * r * 0.85
            sz = p.size
            if self._mode == "speaking":
                sz *= 1.0 + (math.sin(self._phase * 3.0 + p.angle) + 1) * 0.4
            elif self._mode == "thinking":
                sz *= 0.8 + math.sin(self._phase * 2.0 + p.angle) * 0.3
            cv.create_oval(px - sz, py - sz, px + sz, py + sz,
                           fill=glow_c, outline="")

        # ── Glow ring (animated) ──────────────────────────────────────
        pulse = (math.sin(self._phase * 0.8) + 1) * 0.5
        ring_r = _AV_SIZE // 2 + 6 + pulse * 3
        ring_w = 3 if self._mode != "idle" else 1
        cv.create_oval(cx + sway - ring_r, cy_f - ring_r,
                       cx + sway + ring_r, cy_f + ring_r,
                       outline=glow_c, width=ring_w)

        # ── Avatar frame selection (LIVE animation) ───────────────────
        frame_key = "normal"

        # Blink logic: blink every ~4 seconds for 150ms
        self._blink_timer += 1.0 / _FPS
        if self._blink_timer > 3.5 + random.random() * 2.0:
            self._blink_active = True
            self._blink_timer = 0.0

        if self._blink_active:
            frame_key = "blink"
            # Blink lasts ~3 frames (150ms at 24fps)
            if self._blink_timer > 0.15:
                self._blink_active = False
                self._blink_timer = 0.0

        # State-specific frames override blink
        if self._mode == "speaking" and not self._blink_active:
            # Alternate between speak and normal for lip movement effect
            if math.sin(self._phase * 6.0) > 0.2:
                frame_key = "speak"
        elif self._mode == "thinking" and not self._blink_active:
            frame_key = "think"
        elif self._mode == "listening" and not self._blink_active:
            frame_key = "listen"

        # Draw the avatar
        photo = self._frames.get(frame_key, self._frames.get("normal"))
        if photo:
            cv.create_image(int(cx + sway), int(cy_f), image=photo,
                            anchor="center")
        else:
            # Fallback: circle
            r = _AV_SIZE // 2
            cv.create_oval(cx - r, cy_f - r, cx + r, cy_f + r,
                           fill=_SURFACE, outline=glow_c, width=2)
            cv.create_text(cx, cy_f, text="SCAV-E", font=("Segoe UI", 18, "bold"),
                           fill=_TEXT)

        # ── Speaking: sound wave emission ─────────────────────────────
        if self._mode == "speaking":
            for wave_i in range(3):
                wave_phase = self._phase * 2.5 + wave_i * 2.0
                wave_r = _AV_SIZE // 2 + 12 + (wave_phase % 5.0) * 10
                wave_alpha = max(0, 1.0 - (wave_phase % 5.0) / 5.0)
                if wave_alpha > 0.05:
                    r_val = int(glow_rgb[0] * wave_alpha * 0.5)
                    g_val = int(glow_rgb[1] * wave_alpha * 0.5)
                    b_val = int(glow_rgb[2] * wave_alpha * 0.5)
                    wc = f"#{max(10, r_val):02x}{max(14, g_val):02x}{max(20, b_val):02x}"
                    cv.create_oval(cx + sway - wave_r, cy_f - wave_r,
                                   cx + sway + wave_r, cy_f + wave_r,
                                   outline=wc, width=1)

        # ── Voice bars ────────────────────────────────────────────────
        total_w = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        bx_start = cx - total_w // 2
        by_base = cy_f + _AV_SIZE // 2 + 22

        for i in range(_N_BARS):
            h = max(1, int(self._bar_current[i] * _BAR_MAX_H))
            x = bx_start + i * (_BAR_W + _BAR_GAP)
            cv.create_rectangle(x, by_base - h, x + _BAR_W, by_base,
                                fill=glow_c, outline="")
            cv.create_rectangle(x, by_base, x + _BAR_W, by_base + h,
                                fill=glow_c, outline="")

    # ── Animation loop ────────────────────────────────────────────────
    def _start_anim(self) -> None:
        self._tick()

    def _tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        self._phase += 0.1

        # Update bar targets
        if self._mode == "speaking":
            for i in range(_N_BARS):
                center_w = 1.0 - abs(i - _N_BARS // 2) / (_N_BARS // 2) * 0.4
                self._bar_target[i] = max(0.1, random.uniform(0.2, 1.0) * center_w)
        elif self._mode == "thinking":
            for i in range(_N_BARS):
                wave = (math.sin(self._phase * 3.0 + i * 0.3) + 1) * 0.5
                self._bar_target[i] = wave * 0.18
        elif self._mode == "listening":
            for i in range(_N_BARS):
                self._bar_target[i] = 0.02 + (math.sin(self._phase * 0.6 + i * 0.15) + 1) * 0.02
        else:
            for i in range(_N_BARS):
                self._bar_target[i] = 0.0

        # Smooth bar interpolation
        for i in range(_N_BARS):
            diff = self._bar_target[i] - self._bar_current[i]
            self._bar_current[i] += diff * 0.35

        self._render()
        self._anim_id = self.after(1000 // _FPS, self._tick)

    # ══════════════════════════════════════════════════════════════════
    #  LOG
    # ══════════════════════════════════════════════════════════════════
    def _build_log(self) -> None:
        f = ctk.CTkFrame(self, corner_radius=16, fg_color=_CARD,
                         border_width=1, border_color=_BORDER)
        f.pack(fill="both", expand=True, padx=20, pady=8)
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(hdr, text="Activity Log",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=_TEXT2).pack(side="left")

        _mono = "Consolas" if platform.system() == "Windows" else "Menlo"
        self._log = ctk.CTkTextbox(
            f, font=ctk.CTkFont(family=_mono, size=12),
            corner_radius=12, fg_color=_SURFACE,
            text_color=_TEXT, state="disabled", wrap="word", border_width=0)
        self._log.pack(fill="both", expand=True, padx=12, pady=(6, 12))

    # ══════════════════════════════════════════════════════════════════
    #  FOOTER
    # ══════════════════════════════════════════════════════════════════
    def _build_footer(self) -> None:
        ft = ctk.CTkFrame(self, corner_radius=16, fg_color=_CARD,
                          border_width=1, border_color=_BORDER, height=42)
        ft.pack(fill="x", padx=20, pady=(0, 16))
        ft.pack_propagate(False)
        inner = ctk.CTkFrame(ft, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=8)
        self._dot = ctk.CTkLabel(inner, text="●", font=ctk.CTkFont(size=10),
                                 text_color=_MUTED, width=16)
        self._dot.pack(side="left", padx=(0, 6))
        self._status_lbl = ctk.CTkLabel(inner, text="Offline",
                                        font=ctk.CTkFont(size=12), text_color=_TEXT2)
        self._status_lbl.pack(side="left")
        ctk.CTkLabel(inner, text="v9.0", font=ctk.CTkFont(size=11),
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
            "idle": ("OFFLINE", _MUTED),
            "listening": ("● LISTENING", _GREEN),
            "speaking": ("● SPEAKING", _CYAN),
            "thinking": ("● THINKING", _AMBER),
        }
        t, c = labels.get(mode, ("OFFLINE", _MUTED))
        self._av_status.configure(text=t, text_color=c)

    def _do_status(self, text: str) -> None:
        self._status_lbl.configure(text=text)
        lo = text.lower()
        if "listening" in lo:
            self._set_dot(_GREEN, True)
            self._set_mode("listening")
        elif "speaking" in lo:
            self._set_dot(_CYAN, True)
            self._set_mode("speaking")
        elif "thinking" in lo:
            self._set_dot(_AMBER, True)
            self._set_mode("thinking")
        elif "offline" in lo:
            self._set_dot(_MUTED, False)
            self._set_mode("idle")
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
            self._btn.configure(text="■  Stop", fg_color=_RED, hover_color=_RED_H)
            self._do_status("Starting...")
            self.log("Overwatch activated")
        else:
            self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H)
            self._do_status("Offline")
            self.log("Overwatch deactivated")
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
