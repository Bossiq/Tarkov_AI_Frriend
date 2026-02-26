"""
PMC Overwatch GUI — 3D holographic avatar with modern glass-morphism UI.

v0.22.0 features:
  • Holographic post-processing: scanlines, chromatic aberration, glow, flicker
  • Upper-body 3D holographic sprites
  • Glass-morphism UI with gradient accents
  • Alive animation engine (SmoothedNoise, micro-expressions, head nods)
  • OBS Overlay, Persona Editor, Chat History, Language Selector
"""

import json
import logging
import math
import os
import platform
import random
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageTk

logger = logging.getLogger(__name__)

# ── Palette (modernized) ─────────────────────────────────────────────
_BG = "#050810"
_CARD = "#0d1117"
_SURFACE = "#161b22"
_GLASS = "#1c2333"
_GREEN = "#3fb950"
_GREEN_H = "#2ea043"
_RED = "#f85149"
_RED_H = "#da3633"
_AMBER = "#d29922"
_CYAN = "#58a6ff"
_HOLO_CYAN = "#00f0ff"
_HOLO_BLUE = "#0066ff"
_TEXT = "#f0f6fc"
_TEXT2 = "#8b949e"
_MUTED = "#30363d"
_BORDER = "#21262d"
_ACCENT = "#1f6feb"

_CANVAS_W = 420
_CANVAS_H = 440
_FPS = 30

_N_BARS = 35
_BAR_W = 2
_BAR_GAP = 1
_BAR_MAX_H = 16

_GLOW = {"idle": _MUTED, "listening": _GREEN, "thinking": _AMBER, "speaking": _HOLO_CYAN}
_GLOW_RGB = {
    "idle": (48, 54, 61), "listening": (63, 185, 80),
    "thinking": (210, 153, 34), "speaking": (0, 240, 255),
}

_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_PERSONA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.json")


# ═════════════════════════════════════════════════════════════════════
# Smoothed noise for organic motion
# ═════════════════════════════════════════════════════════════════════
class _SmoothedNoise:
    def __init__(self, speed: float = 0.5, amplitude: float = 1.0) -> None:
        self._speed = speed
        self._amp = amplitude
        self._seed = random.uniform(0, 1000)

    def sample(self, t: float) -> float:
        p = (t * self._speed) + self._seed
        return (math.sin(p * 1.0) * 0.5
                + math.sin(p * 2.3 + 1.7) * 0.3
                + math.sin(p * 4.1 + 3.2) * 0.2) * self._amp


def _lerp(a, b, t):
    return a + (b - a) * t


def _build_region_mask(w, h, top, bot, fade):
    mask = Image.new("L", (w, h), 0)
    px = mask.load()
    tp, bp, fp = int(top * h), int(bot * h), max(1, int(fade * h))
    for y in range(h):
        if y < tp or y >= bp:
            a = 0
        elif y < tp + fp:
            a = int(255 * (y - tp) / fp)
        elif y >= bp - fp:
            a = int(255 * (bp - y) / fp)
        else:
            a = 255
        for x in range(w):
            px[x, y] = a
    return mask


# ═════════════════════════════════════════════════════════════════════
# Holographic Post-Processing Pipeline
# ═════════════════════════════════════════════════════════════════════
class _HoloFX:
    """Real-time holographic effects applied per frame.

    Effects: scanlines, chromatic aberration, edge glow, flicker.
    Optimized: precomputed scanline overlay, minimal per-frame work.
    """

    def __init__(self, size: int) -> None:
        self._size = size
        # Precompute scanline overlay (reused every frame)
        self._scanlines = self._make_scanlines(size)
        self._frame_count = 0

    @staticmethod
    def _make_scanlines(size: int) -> Image.Image:
        """Semi-transparent horizontal scanlines."""
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        for y in range(0, size, 3):
            draw.line([(0, y), (size, y)], fill=(0, 0, 0, 35), width=1)
        return img

    def apply(self, frame: Image.Image, t: float, mode: str) -> Image.Image:
        """Apply holographic effects to a rendered frame."""
        self._frame_count += 1
        w, h = frame.size

        # ── 1. Chromatic aberration (RGB channel offset) ──────────────
        r, g, b, a = frame.split()
        offset = 2
        r_shifted = Image.new("L", (w, h), 0)
        r_shifted.paste(r, (offset, 0))
        b_shifted = Image.new("L", (w, h), 0)
        b_shifted.paste(b, (-offset, 0))
        frame = Image.merge("RGBA", (r_shifted, g, b_shifted, a))

        # ── 2. Scanlines overlay ──────────────────────────────────────
        frame = Image.alpha_composite(frame, self._scanlines)

        # ── 3. Edge glow (subtle bloom) ───────────────────────────────
        if self._frame_count % 3 == 0:  # every 3rd frame for perf
            glow = frame.filter(ImageFilter.GaussianBlur(radius=6))
            enhancer = ImageEnhance.Brightness(glow)
            glow = enhancer.enhance(0.3)
            frame = Image.alpha_composite(frame, glow)

        # ── 4. Flicker (subtle brightness variation) ──────────────────
        flicker = 0.95 + math.sin(t * 12.0) * 0.03 + random.uniform(-0.01, 0.01)
        if flicker != 1.0:
            enhancer = ImageEnhance.Brightness(frame)
            frame = enhancer.enhance(max(0.85, min(1.05, flicker)))

        # ── 5. Holographic tint (subtle cyan-blue push) ───────────────
        if mode != "idle":
            tint = Image.new("RGBA", (w, h), (0, 180, 255, 12))
            frame = Image.alpha_composite(frame, tint)

        return frame


# ═════════════════════════════════════════════════════════════════════
# Alive Holographic Engine
# ═════════════════════════════════════════════════════════════════════
class _AliveEngine:
    """3D holographic sprite compositing with organic animation."""

    _SPRITE_FILES = {
        "neutral": "neutral.png", "talk_a": "talk_a.png",
        "talk_b": "talk_b.png", "blink": "blink.png",
        "think": "think.png", "smile": "smile.png",
    }
    _MOUTH_TOP, _MOUTH_BOT, _MOUTH_FADE = 0.55, 0.82, 0.08
    _EYE_TOP, _EYE_BOT, _EYE_FADE = 0.22, 0.48, 0.06

    def __init__(self, size: int) -> None:
        self._size = size
        self._sprites: dict[str, Image.Image] = {}
        self._mouth_mask: Optional[Image.Image] = None
        self._eye_mask: Optional[Image.Image] = None
        self._holo = _HoloFX(size)
        self._load()

        # Noise channels
        self.n_head_x = _SmoothedNoise(0.4, 7.0)
        self.n_head_y = _SmoothedNoise(0.35, 4.0)
        self.n_scale = _SmoothedNoise(0.6, 0.012)
        self._breath_phase = random.uniform(0, math.pi)

        # Blend state
        self.mouth_blend = 0.0
        self.mouth_wide = False
        self.blink_blend = 0.0
        self.smile_blend = 0.0
        self.think_blend = 0.0
        self._micro_timer = 0.0
        self._micro_blend = 0.0
        self._micro_target = 0.0

    def _load(self):
        for name, fn in self._SPRITE_FILES.items():
            path = os.path.join(_ASSET_DIR, fn)
            if os.path.exists(path):
                try:
                    img = Image.open(path).convert("RGBA")
                    img = img.resize((self._size, self._size), Image.LANCZOS)
                    self._sprites[name] = img
                except Exception:
                    logger.warning("Failed to load: %s", path)
        if "neutral" not in self._sprites:
            self._sprites["neutral"] = Image.new("RGBA", (self._size, self._size), (0, 20, 30, 255))
        for fb in self._SPRITE_FILES:
            if fb not in self._sprites:
                self._sprites[fb] = self._sprites["neutral"]
        s = self._size
        self._mouth_mask = _build_region_mask(s, s, self._MOUTH_TOP, self._MOUTH_BOT, self._MOUTH_FADE)
        self._eye_mask = _build_region_mask(s, s, self._EYE_TOP, self._EYE_BOT, self._EYE_FADE)

    def update_micro(self, dt):
        self._micro_timer += dt
        if self._micro_timer > random.uniform(3.0, 8.0):
            self._micro_timer = 0.0
            self._micro_target = random.uniform(0.0, 0.3)
        self._micro_blend = _lerp(self._micro_blend, self._micro_target, 0.03)
        if abs(self._micro_blend - self._micro_target) < 0.01:
            self._micro_target = 0.0

    def render(self, t: float, amplitude: float, mode: str) -> Image.Image:
        s = self._size
        base = self._sprites["neutral"].copy()

        # Mouth
        if self.mouth_blend > 0.03:
            talk = self._sprites["talk_b" if self.mouth_wide else "talk_a"]
            scaled = self._mouth_mask.point(lambda p: int(p * min(1.0, self.mouth_blend)))
            base.paste(talk, mask=scaled)

        # Eyes
        if self.blink_blend > 0.03:
            scaled = self._eye_mask.point(lambda p: int(p * min(1.0, self.blink_blend)))
            base.paste(self._sprites["blink"], mask=scaled)

        # Expression
        eff_smile = max(self.smile_blend, self._micro_blend)
        if eff_smile > 0.03:
            base = Image.blend(base, self._sprites["smile"], min(0.6, eff_smile * 0.6))
        if self.think_blend > 0.03:
            base = Image.blend(base, self._sprites["think"], min(0.5, self.think_blend * 0.5))

        # Motion
        w, h = base.size
        breath = math.sin(self._breath_phase) * 4.0
        breath_scale = 1.0 + math.sin(self._breath_phase * 0.5) * 0.012
        dx = int(self.n_head_x.sample(t))
        dy = int(self.n_head_y.sample(t) + breath)

        if mode == "speaking" and amplitude > 0.15:
            dy += int(math.sin(t * 8.0) * amplitude * 3.0)
        if mode == "listening":
            dy += 3

        scale = breath_scale + self.n_scale.sample(t)
        nw, nh = int(w * scale), int(h * scale)

        if nw != w or nh != h:
            base = base.resize((nw, nh), Image.LANCZOS)
            cx = max(0, min(nw - w, (nw - w) // 2 - dx))
            cy = max(0, min(nh - h, (nh - h) // 2 - dy))
            base = base.crop((cx, cy, cx + w, cy + h))
        elif abs(dx) > 0 or abs(dy) > 0:
            shifted = Image.new("RGBA", (w, h), (5, 8, 16, 255))
            shifted.paste(base, (max(-w // 4, min(w // 4, dx)), max(-h // 4, min(h // 4, dy))))
            base = shifted

        # Apply holographic post-processing
        base = self._holo.apply(base, t, mode)

        return base


# ═════════════════════════════════════════════════════════════════════
# Particles
# ═════════════════════════════════════════════════════════════════════
class _Particle:
    __slots__ = ("x", "y", "vx", "vy", "r", "alpha", "life", "max_life")
    def __init__(self, cx, cy):
        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(80, 170)
        self.x = cx + math.cos(angle) * dist
        self.y = cy + math.sin(angle) * dist
        self.vx = random.uniform(-0.3, 0.3)
        self.vy = random.uniform(-0.5, -0.1)
        self.r = random.uniform(1.0, 2.5)
        self.alpha = 0.0
        self.life = 0.0
        self.max_life = random.uniform(3.0, 6.0)


# ═════════════════════════════════════════════════════════════════════
# Main GUI — Modern Glass-morphism Design
# ═════════════════════════════════════════════════════════════════════
class OverwatchGUI(ctk.CTk):
    """PMC Overwatch — 3D holographic avatar with glass-morphism UI."""

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("PMC Overwatch")
        self.geometry("460x820")
        self.minsize(420, 740)
        self.configure(fg_color=_BG)
        self.resizable(True, True)

        self.shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._toggle_cb: Optional[Callable[[bool], None]] = None
        self._is_running = False
        self._obs_mode = False

        self._session_start = datetime.now()
        self._words_spoken = 0
        self._responses = 0
        self._chat_log: list[str] = []
        Path(_LOG_DIR).mkdir(exist_ok=True)

        self._mode = "idle"
        self._time = 0.0
        self._photo: Optional[ImageTk.PhotoImage] = None

        avatar_size = min(_CANVAS_W - 40, _CANVAS_H - 60)
        self._engine = _AliveEngine(avatar_size)
        self._avatar_x = (_CANVAS_W - avatar_size) // 2
        self._avatar_y = 6

        # Blink
        self._blink_timer = 0.0
        self._blink_cd = random.uniform(2.0, 5.0)
        self._blink_stage = 0
        self._blink_dur = 0.0
        self._double_blink = False
        self._double_blink_count = 0

        # Amplitude
        self._amplitude = 0.0
        self._amplitude_target = 0.0

        # Emotion
        self._emotion = "neutral"
        self._emotion_timer = 0.0

        # Particles
        self._particles: list[_Particle] = []
        self._particle_timer = 0.0

        # Settings
        self._input_mode = os.getenv("INPUT_MODE", "auto").lower()
        self._language = os.getenv("WHISPER_LANGUAGE", "auto").lower()

        # Bars
        self._bar_target = [0.0] * _N_BARS
        self._bar_current = [0.0] * _N_BARS

        self._build_header()
        self._build_agent()
        self._build_log()
        self._build_footer()
        self._start_anim()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-o>", lambda e: self._toggle_obs())
        self.bind("<Control-p>", lambda e: self._open_persona_editor())
        logger.info("Holographic GUI initialized (v0.22.0)")

    # ══ HEADER ═══════════════════════════════════════════════════════
    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, corner_radius=20, fg_color=_CARD,
                           border_width=1, border_color=_BORDER)
        hdr.pack(fill="x", padx=24, pady=(20, 0))
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=14)
        logo = ctk.CTkFrame(inner, fg_color="transparent")
        logo.pack(side="left")
        _ft = "Segoe UI" if platform.system() == "Windows" else "SF Pro Display"
        ctk.CTkLabel(logo, text="PMC Overwatch",
                     font=ctk.CTkFont(family=_ft, size=24, weight="bold"),
                     text_color=_TEXT).pack(anchor="w")
        ctk.CTkLabel(logo, text="Holographic AI Companion",
                     font=ctk.CTkFont(family=_ft, size=11),
                     text_color=_HOLO_CYAN).pack(anchor="w")
        self._btn = ctk.CTkButton(
            inner, text="▶  Start", width=140, height=44, corner_radius=14,
            font=ctk.CTkFont(family=_ft, size=14, weight="bold"),
            fg_color=_GREEN, hover_color=_GREEN_H, text_color="white",
            command=self._on_toggle)
        self._btn.pack(side="right")

    # ══ CANVAS ═══════════════════════════════════════════════════════
    def _build_agent(self) -> None:
        self._cv = tk.Canvas(self, width=_CANVAS_W, height=_CANVAS_H,
                             bg=_BG, highlightthickness=0, bd=0)
        self._cv.pack(pady=(8, 0))
        self._av_status = ctk.CTkLabel(
            self, text="◆ OFFLINE",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_MUTED)
        self._av_status.pack(pady=(2, 4))

    # ══ RENDERING ════════════════════════════════════════════════════
    def _render_frame(self) -> None:
        cv = self._cv
        cv.delete("all")
        glow_c = _GLOW.get(self._mode, _MUTED)
        glow_rgb = _GLOW_RGB.get(self._mode, (48, 54, 61))
        cx, cy = _CANVAS_W // 2, _CANVAS_H // 2 - 10

        # ── Holographic base plate glow ───────────────────────────────
        if self._mode != "idle":
            # Soft ambient glow
            glow_a = 20 + int((math.sin(self._time * 0.5) + 1) * 8)
            r, g, b = glow_rgb
            gc = f"#{max(5, r * glow_a // 255):02x}{max(8, g * glow_a // 255):02x}{max(16, b * glow_a // 255):02x}"
            ar = self._engine._size // 2 + 20
            cv.create_oval(cx - ar, cy - ar, cx + ar, cy + ar, fill=gc, outline="")

            # Holographic ring (brighter, more sci-fi)
            for i in range(4):
                rr = ar - 5 + i * 6 + int(math.sin(self._time * 0.7 + i) * 2)
                a = max(0, 55 - i * 15)
                rc = max(5, int(r * a / 255))
                gc2 = max(8, int(g * a / 255))
                bc = max(16, int(b * a / 255))
                c = f"#{rc:02x}{gc2:02x}{bc:02x}"
                cv.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=c, width=1)

        # ── Particles ─────────────────────────────────────────────────
        for p in self._particles:
            if p.alpha > 0.02:
                a = int(min(1.0, p.alpha) * 80)
                pr = max(5, int(glow_rgb[0] * a / 255))
                pg = max(8, int(glow_rgb[1] * a / 255))
                pb = max(16, int(glow_rgb[2] * a / 255))
                pc = f"#{pr:02x}{pg:02x}{pb:02x}"
                cv.create_oval(p.x - p.r, p.y - p.r, p.x + p.r, p.y + p.r,
                               fill=pc, outline="")

        # ── Avatar ────────────────────────────────────────────────────
        frame = self._engine.render(self._time, self._amplitude, self._mode)
        if frame is not None:
            self._photo = ImageTk.PhotoImage(frame)
            cv.create_image(self._avatar_x, self._avatar_y,
                            image=self._photo, anchor="nw")

        # ── Waveform ─────────────────────────────────────────────────
        total = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        bx = _CANVAS_W // 2 - total // 2
        by = _CANVAS_H - 20
        for i in range(_N_BARS):
            h = max(1, int(self._bar_current[i] * _BAR_MAX_H))
            x = bx + i * (_BAR_W + _BAR_GAP)
            cv.create_rectangle(x, by - h, x + _BAR_W, by, fill=glow_c, outline="")
            cv.create_rectangle(x, by, x + _BAR_W, by + h, fill=glow_c, outline="")

    # ══ ANIMATION ════════════════════════════════════════════════════
    def _update_state(self, dt: float) -> None:
        mode = self._mode
        eng = self._engine

        eng._breath_phase += dt * 1.2
        eng.update_micro(dt)

        # Blink
        self._blink_timer += dt
        if self._blink_stage == 0:
            if self._blink_timer >= self._blink_cd:
                self._blink_stage = 1; self._blink_dur = 0.0
                self._double_blink = random.random() < 0.15
                self._double_blink_count = 0
        elif self._blink_stage == 1:
            self._blink_dur += dt
            eng.blink_blend = _lerp(eng.blink_blend, 0.6, 0.45)
            if self._blink_dur >= 0.05:
                self._blink_stage = 2; self._blink_dur = 0.0
        elif self._blink_stage == 2:
            self._blink_dur += dt
            eng.blink_blend = _lerp(eng.blink_blend, 1.0, 0.55)
            if self._blink_dur >= 0.08:
                self._blink_stage = 3; self._blink_dur = 0.0
        elif self._blink_stage == 3:
            self._blink_dur += dt
            eng.blink_blend = _lerp(eng.blink_blend, 0.0, 0.35)
            if self._blink_dur >= 0.07:
                eng.blink_blend = 0.0
                if self._double_blink and self._double_blink_count == 0:
                    self._double_blink_count = 1; self._blink_stage = 0
                    self._blink_cd = random.uniform(0.15, 0.3)
                else:
                    self._blink_stage = 0; self._blink_timer = 0.0
                    self._blink_cd = random.uniform(2.0, 5.0)

        # Amplitude
        self._amplitude += (self._amplitude_target - self._amplitude) * 0.4

        # Mode blends
        if mode == "speaking":
            eng.mouth_blend = min(1.0, self._amplitude * 2.0)
            eng.mouth_wide = self._amplitude > 0.4
            eng.smile_blend = _lerp(eng.smile_blend, 0.0, 0.08)
            eng.think_blend = _lerp(eng.think_blend, 0.0, 0.08)
        elif mode == "thinking":
            eng.mouth_blend = _lerp(eng.mouth_blend, 0.0, 0.1)
            eng.smile_blend = _lerp(eng.smile_blend, 0.0, 0.08)
            eng.think_blend = _lerp(eng.think_blend, 0.85, 0.06)
        elif mode == "listening":
            eng.mouth_blend = _lerp(eng.mouth_blend, 0.0, 0.1)
            eng.smile_blend = _lerp(eng.smile_blend, 0.2, 0.04)
            eng.think_blend = _lerp(eng.think_blend, 0.0, 0.08)
        else:
            eng.mouth_blend = _lerp(eng.mouth_blend, 0.0, 0.08)
            eng.smile_blend = _lerp(eng.smile_blend, 0.0, 0.04)
            eng.think_blend = _lerp(eng.think_blend, 0.0, 0.04)

        if self._emotion == "happy":
            eng.smile_blend = _lerp(eng.smile_blend, 0.9, 0.06)
            self._emotion_timer += dt
            if self._emotion_timer > 3.0:
                self._emotion = "neutral"; self._emotion_timer = 0.0

        # Particles
        self._particle_timer += dt
        if self._particle_timer > 0.2 and mode != "idle" and len(self._particles) < 20:
            self._particle_timer = 0.0
            self._particles.append(_Particle(_CANVAS_W // 2, _CANVAS_H // 2))
        alive = []
        for p in self._particles:
            p.life += dt; p.x += p.vx; p.y += p.vy
            p.alpha = min(1.0, p.life / 0.5) * max(0.0, 1.0 - (p.life - p.max_life + 1.0))
            if p.life < p.max_life:
                alive.append(p)
        self._particles = alive

        # Bars
        for i in range(_N_BARS):
            if mode in ("speaking", "listening"):
                wave = math.sin(self._time * 3.0 + i * 0.35) * 0.3 + 0.5
                self._bar_target[i] = wave * random.uniform(0.3, 1.0)
            else:
                self._bar_target[i] = 0.0
            self._bar_current[i] = _lerp(self._bar_current[i], self._bar_target[i], 0.18)

    def _start_anim(self):
        self._tick()

    def _tick(self):
        if self.shutdown_event.is_set(): return
        dt = 1.0 / _FPS
        self._time += dt
        self._update_state(dt)
        self._render_frame()
        self.after(int(1000 / _FPS), self._tick)

    # ══ LOG PANEL (Glass-morphism) ═══════════════════════════════════
    def _build_log(self) -> None:
        wrapper = ctk.CTkFrame(self, corner_radius=20, fg_color=_CARD,
                               border_width=1, border_color=_BORDER)
        wrapper.pack(fill="both", expand=True, padx=24, pady=(6, 0))
        inner = ctk.CTkFrame(wrapper, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=14, pady=14)

        top_bar = ctk.CTkFrame(inner, fg_color="transparent")
        top_bar.pack(fill="x")
        ctk.CTkLabel(top_bar, text="◆ Activity Log",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=_TEXT2).pack(side="left")

        self._lang_var = ctk.StringVar(value=self._language.upper())
        lang_menu = ctk.CTkOptionMenu(
            top_bar, values=["AUTO", "EN", "RU", "RO"],
            variable=self._lang_var, width=80, height=24,
            font=ctk.CTkFont(size=10), fg_color=_GLASS,
            button_color=_BORDER, command=self._on_lang_change)
        lang_menu.pack(side="right")
        ctk.CTkLabel(top_bar, text="🌐", font=ctk.CTkFont(size=12),
                     text_color=_HOLO_CYAN).pack(side="right", padx=(0, 6))

        self._log = ctk.CTkTextbox(
            inner, font=ctk.CTkFont(family="Cascadia Code, Consolas", size=11),
            height=110, fg_color=_SURFACE, text_color=_TEXT, corner_radius=12,
            border_width=1, border_color=_BORDER, wrap="word")
        self._log.pack(fill="both", expand=True, pady=(8, 0))
        self._log.configure(state="disabled")

        _chat_in = ctk.CTkFrame(inner, fg_color="transparent")
        _chat_in.pack(fill="x", pady=(10, 0))
        self._chat_entry = ctk.CTkEntry(
            _chat_in, placeholder_text="Type a message…",
            font=ctk.CTkFont(size=12), fg_color=_GLASS,
            text_color=_TEXT, border_color=_BORDER, corner_radius=12,
            height=38)
        self._chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._chat_entry.bind("<Return>", self._on_chat_send)
        self._chat_btn = ctk.CTkButton(
            _chat_in, text="Send", width=70, height=38, corner_radius=12,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=_ACCENT, hover_color="#1a7f37", text_color="white",
            command=self._on_chat_send)
        self._chat_btn.pack(side="right")

    # ══ FOOTER ═══════════════════════════════════════════════════════
    def _build_footer(self) -> None:
        ftr = ctk.CTkFrame(self, corner_radius=14, fg_color=_CARD,
                           border_width=1, border_color=_BORDER, height=40)
        ftr.pack(fill="x", padx=24, pady=(6, 16))
        inner = ctk.CTkFrame(ftr, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=8)
        self._dot = tk.Canvas(inner, width=10, height=10, bg=_CARD,
                              highlightthickness=0, bd=0)
        self._dot.pack(side="left", padx=(0, 8))
        self._dot_id = self._dot.create_oval(1, 1, 9, 9, fill=_MUTED, outline="")
        self._status_lbl = ctk.CTkLabel(inner, text="Offline",
                                        font=ctk.CTkFont(size=12), text_color=_TEXT2)
        self._status_lbl.pack(side="left")
        ctk.CTkLabel(inner, text="Ctrl+O  Ctrl+P",
                     font=ctk.CTkFont(size=9), text_color=_MUTED).pack(side="right", padx=(0, 10))
        ctk.CTkLabel(inner, text="v0.22.0", font=ctk.CTkFont(size=10),
                     text_color=_MUTED).pack(side="right", padx=(0, 10))

    # ══ OBS / PERSONA / LANG ═════════════════════════════════════════
    def _toggle_obs(self):
        self._obs_mode = not self._obs_mode
        if self._obs_mode:
            self.overrideredirect(True)
            self.attributes("-topmost", True)
            try: self.attributes("-transparentcolor", _BG)
            except Exception: pass
            self.log("🎬 OBS Overlay ON")
        else:
            self.overrideredirect(False)
            self.attributes("-topmost", False)
            try: self.attributes("-transparentcolor", "")
            except Exception: pass
            self.log("🎬 OBS Overlay OFF")

    def _open_persona_editor(self):
        win = ctk.CTkToplevel(self)
        win.title("Persona Editor")
        win.geometry("520x420")
        win.configure(fg_color=_BG)
        win.attributes("-topmost", True)
        ctk.CTkLabel(win, text="🛡️ Persona Editor",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=_TEXT).pack(pady=(20, 8))
        ctk.CTkLabel(win, text="Edit AI personality and system prompt",
                     font=ctk.CTkFont(size=11), text_color=_TEXT2).pack()
        persona = self._load_persona()
        frame = ctk.CTkFrame(win, fg_color=_CARD, corner_radius=16)
        frame.pack(fill="both", expand=True, padx=24, pady=16)
        ctk.CTkLabel(frame, text="Name:", text_color=_TEXT2).pack(anchor="w", padx=16, pady=(16, 0))
        name_e = ctk.CTkEntry(frame, fg_color=_GLASS, text_color=_TEXT, border_color=_BORDER, corner_radius=10)
        name_e.pack(fill="x", padx=16, pady=(4, 10))
        name_e.insert(0, persona.get("name", "PMC Operator"))
        ctk.CTkLabel(frame, text="System Prompt:", text_color=_TEXT2).pack(anchor="w", padx=16)
        p_box = ctk.CTkTextbox(frame, fg_color=_GLASS, text_color=_TEXT, corner_radius=10, height=180, wrap="word")
        p_box.pack(fill="both", expand=True, padx=16, pady=(4, 16))
        p_box.insert("1.0", persona.get("prompt", ""))
        def save():
            self._save_persona({"name": name_e.get().strip(), "prompt": p_box.get("1.0", "end").strip()})
            self.log("🛡️ Persona saved"); win.destroy()
        ctk.CTkButton(win, text="💾 Save", width=130, height=38, corner_radius=12,
                      fg_color=_GREEN, hover_color=_GREEN_H, text_color="white", command=save).pack(pady=(0, 20))

    def _load_persona(self):
        try:
            if os.path.exists(_PERSONA_FILE):
                with open(_PERSONA_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: pass
        return {"name": "PMC Operator", "prompt": ""}

    def _save_persona(self, data):
        try:
            with open(_PERSONA_FILE, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception: logger.exception("Failed to save persona")

    def _on_lang_change(self, value):
        os.environ["WHISPER_LANGUAGE"] = value.lower()
        self._language = value.lower()
        self.log(f"🌐 Language → {value}")

    # ══ PUBLIC API ════════════════════════════════════════════════════
    def set_toggle_callback(self, cb): self._toggle_cb = cb
    def register_thread(self, t): self._threads.append(t)
    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}]  {msg}"; self._chat_log.append(line)
        self.after(0, self._do_log, f"{line}\n")
    def set_status(self, text): self.after(0, self._do_status, text)
    def force_toggle_off(self): self.after(0, self._force_off)
    def set_vis_mode(self, mode): self.after(0, self._set_mode, mode)
    def set_amplitude(self, amp): self._amplitude_target = amp
    def set_emotion(self, emotion): self.after(0, self._do_emotion, emotion)
    def add_response_stats(self, wc): self._words_spoken += wc; self._responses += 1

    # ══ INTERNAL ═════════════════════════════════════════════════════
    def _do_log(self, line):
        self._log.configure(state="normal"); self._log.insert("end", line)
        self._log.see("end"); self._log.configure(state="disabled")

    def _set_mode(self, mode):
        self._mode = mode
        labels = {"idle": ("◆ OFFLINE", _MUTED), "listening": ("◆ LISTENING", _GREEN),
                  "thinking": ("◆ THINKING", _AMBER), "speaking": ("◆ SPEAKING", _HOLO_CYAN)}
        t, c = labels.get(mode, ("◆ OFFLINE", _MUTED))
        self._av_status.configure(text=t, text_color=c)

    def _do_emotion(self, e): self._emotion = e; self._emotion_timer = 0.0

    def _do_status(self, text):
        self._status_lbl.configure(text=text)
        lo = text.lower()
        if "listening" in lo: self._set_dot(_GREEN, True); self._set_mode("listening")
        elif "speaking" in lo: self._set_dot(_HOLO_CYAN, True); self._set_mode("speaking")
        elif "thinking" in lo: self._set_dot(_AMBER, True); self._set_mode("thinking")
        elif "offline" in lo: self._set_dot(_MUTED, False); self._set_mode("idle")
        else: self._set_dot(_AMBER, False)

    def _set_dot(self, color, pulse): self._dot.itemconfig(self._dot_id, fill=color)

    def _on_toggle(self):
        self._is_running = not self._is_running
        if self._is_running:
            self._btn.configure(text="■  Stop", fg_color=_RED, hover_color=_RED_H)
            self._set_mode("listening")
        else:
            self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H)
            self._set_mode("idle")
        if self._toggle_cb: self._toggle_cb(self._is_running)

    def _force_off(self):
        self._is_running = False
        self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H)
        self._set_mode("idle")

    def _on_chat_send(self, event=None):
        text = self._chat_entry.get().strip()
        if text and self._toggle_cb:
            self._chat_entry.delete(0, "end"); self.log(f"[You] {text}")
            import threading as _t
            _t.Thread(target=self._toggle_cb, args=(True,), daemon=True).start()

    def _save_chat_history(self):
        if self._chat_log:
            ts = self._session_start.strftime("%Y%m%d_%H%M%S")
            try:
                with open(os.path.join(_LOG_DIR, f"session_{ts}.log"), "w", encoding="utf-8") as f:
                    f.write("\n".join(self._chat_log))
            except Exception: pass

    def _on_close(self):
        self.shutdown_event.set(); self._save_chat_history()
        for t in self._threads:
            if t.is_alive(): t.join(timeout=2.0)
        try: self.destroy()
        except Exception: pass


if __name__ == "__main__":
    app = OverwatchGUI()
    import threading, time, random as _r
    def _demo():
        time.sleep(1.5)
        app.after(0, lambda: app.set_vis_mode("listening"))
        time.sleep(2)
        app.after(0, lambda: app.set_vis_mode("speaking"))
        for _ in range(50):
            app.set_amplitude(_r.uniform(0.0, 1.0))
            time.sleep(0.05)
        app.set_amplitude(0.0)
        time.sleep(0.5)
        app.after(0, lambda: app.set_emotion("happy"))
        time.sleep(2)
        app.after(0, lambda: app.set_vis_mode("thinking"))
        time.sleep(2)
        app.after(0, lambda: app.set_vis_mode("idle"))
        time.sleep(2)
        app.after(0, app._on_close)
    threading.Thread(target=_demo, daemon=True).start()
    app.mainloop()
    print("HOLOGRAPHIC V22 DEMO PASSED")
