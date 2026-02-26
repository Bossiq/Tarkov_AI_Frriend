"""
PMC Overwatch GUI — Alive avatar that feels like a real co-streamer.

Philosophy: the character is NEVER still. Every parameter animates
continuously using smoothed noise functions, creating organic movement
that mimics a real person on webcam. Key animations:

  • Perlin-like smoothed noise drives head position, tilt, gaze
  • Breathing visibly shifts sprite up/down and scales
  • Amplitude-synced head nods during speech
  • Micro-expressions: random smile flickers, brow raises
  • Listening lean: head tilts forward when listening
  • Eye wander: gaze drifts to random targets smoothly
  • All transitions use exponential smoothing (no linear jumps)
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
from PIL import Image, ImageTk

logger = logging.getLogger(__name__)

# ── Palette ──────────────────────────────────────────────────────────
_BG = "#0a0e14"
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

_CANVAS_W = 400
_CANVAS_H = 420
_FPS = 30

_N_BARS = 31
_BAR_W = 3
_BAR_GAP = 1
_BAR_MAX_H = 14

_GLOW = {"idle": _MUTED, "listening": _GREEN, "thinking": _AMBER, "speaking": _CYAN}
_GLOW_RGB = {
    "idle": (61, 68, 80), "listening": (0, 210, 106),
    "thinking": (255, 165, 2), "speaking": (0, 210, 255),
}

_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_PERSONA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.json")


# ═════════════════════════════════════════════════════════════════════
# Smoothed noise — organic random motion (like a real person on cam)
# ═════════════════════════════════════════════════════════════════════
class _SmoothedNoise:
    """Multi-octave smoothed random values for organic motion.

    Unlike linear interpolation between random targets, this produces
    flowing, continuous motion that mimics biological movement.
    """
    def __init__(self, speed: float = 0.5, amplitude: float = 1.0) -> None:
        self._speed = speed
        self._amp = amplitude
        self._phase = random.uniform(0, 100)
        self._seed = random.uniform(0, 1000)

    def sample(self, t: float) -> float:
        p = (t * self._speed) + self._seed
        # Three sine waves at different frequencies for organic feel
        v = (math.sin(p * 1.0) * 0.5
             + math.sin(p * 2.3 + 1.7) * 0.3
             + math.sin(p * 4.1 + 3.2) * 0.2)
        return v * self._amp


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _build_region_mask(w: int, h: int, top: float, bot: float, fade: float) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    px = mask.load()
    tp, bp = int(top * h), int(bot * h)
    fp = max(1, int(fade * h))
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
# Alive Engine — sprite + continuous organic animation
# ═════════════════════════════════════════════════════════════════════
class _AliveEngine:
    """Sprite compositing with organic, continuous animation.

    Key principle: NEVER be still. Every visible parameter
    is driven by smoothed noise functions that produce
    flowing, biological-feeling motion.
    """

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
        self._load()

        # Organic noise channels
        self.n_head_x = _SmoothedNoise(speed=0.4, amplitude=8.0)
        self.n_head_y = _SmoothedNoise(speed=0.35, amplitude=5.0)
        self.n_tilt = _SmoothedNoise(speed=0.25, amplitude=2.5)     # rotation degrees
        self.n_scale = _SmoothedNoise(speed=0.6, amplitude=0.015)   # subtle zoom
        self.n_gaze_x = _SmoothedNoise(speed=0.3, amplitude=4.0)    # eye drift
        self.n_gaze_y = _SmoothedNoise(speed=0.25, amplitude=2.5)

        # Breath rhythm (sine-based, not noise)
        self._breath_phase = random.uniform(0, math.pi)

        # Expression blends
        self.mouth_blend = 0.0
        self.mouth_wide = False
        self.blink_blend = 0.0
        self.smile_blend = 0.0
        self.think_blend = 0.0

        # Micro-expression state
        self._micro_timer = 0.0
        self._micro_blend = 0.0  # current micro-smile
        self._micro_target = 0.0

    def _load(self) -> None:
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
            self._sprites["neutral"] = Image.new("RGBA", (self._size, self._size), (20, 20, 30, 255))
        for fb in self._SPRITE_FILES:
            if fb not in self._sprites:
                self._sprites[fb] = self._sprites["neutral"]

        s = self._size
        self._mouth_mask = _build_region_mask(s, s, self._MOUTH_TOP, self._MOUTH_BOT, self._MOUTH_FADE)
        self._eye_mask = _build_region_mask(s, s, self._EYE_TOP, self._EYE_BOT, self._EYE_FADE)

    def update_micro(self, dt: float) -> None:
        """Random micro-expressions — slight smile flickers like a real person."""
        self._micro_timer += dt
        if self._micro_timer > random.uniform(3.0, 8.0):
            self._micro_timer = 0.0
            self._micro_target = random.uniform(0.0, 0.35)
        self._micro_blend = _lerp(self._micro_blend, self._micro_target, 0.03)
        if abs(self._micro_blend - self._micro_target) < 0.01:
            self._micro_target = 0.0  # fade back

    def render(self, t: float, amplitude: float, mode: str) -> Image.Image:
        s = self._size
        base = self._sprites["neutral"].copy()

        # ── Layer 1: Mouth (amplitude-driven) ─────────────────────────
        if self.mouth_blend > 0.03:
            talk = self._sprites["talk_b" if self.mouth_wide else "talk_a"]
            scaled = self._mouth_mask.point(lambda p: int(p * min(1.0, self.mouth_blend)))
            base.paste(talk, mask=scaled)

        # ── Layer 2: Eyes (blink) ─────────────────────────────────────
        if self.blink_blend > 0.03:
            scaled = self._eye_mask.point(lambda p: int(p * min(1.0, self.blink_blend)))
            base.paste(self._sprites["blink"], mask=scaled)

        # ── Layer 3: Expression ───────────────────────────────────────
        effective_smile = max(self.smile_blend, self._micro_blend)
        if effective_smile > 0.03:
            base = Image.blend(base, self._sprites["smile"], min(0.6, effective_smile * 0.6))
        if self.think_blend > 0.03:
            base = Image.blend(base, self._sprites["think"], min(0.5, self.think_blend * 0.5))

        # ── Layer 4: Organic motion (never still) ─────────────────────
        w, h = base.size

        # Breathing (always active, visible)
        breath = math.sin(self._breath_phase) * 4.0
        breath_scale = 1.0 + math.sin(self._breath_phase * 0.5) * 0.012

        # Noise-driven head motion
        dx = int(self.n_head_x.sample(t))
        dy = int(self.n_head_y.sample(t) + breath)

        # Head nods synced to speech amplitude
        if mode == "speaking" and amplitude > 0.15:
            nod = math.sin(t * 8.0) * amplitude * 3.0
            dy += int(nod)

        # Listening lean (tilt forward)
        if mode == "listening":
            dy += 3  # slight lean forward

        # Scale from breathing + noise
        scale = breath_scale + self.n_scale.sample(t)
        nw = int(w * scale)
        nh = int(h * scale)

        if nw != w or nh != h:
            base = base.resize((nw, nh), Image.LANCZOS)
            cx = max(0, min(nw - w, (nw - w) // 2 - dx))
            cy = max(0, min(nh - h, (nh - h) // 2 - dy))
            base = base.crop((cx, cy, cx + w, cy + h))
        elif abs(dx) > 0 or abs(dy) > 0:
            shifted = Image.new("RGBA", (w, h), (10, 14, 20, 255))
            shifted.paste(base, (max(-w // 4, min(w // 4, dx)), max(-h // 4, min(h // 4, dy))))
            base = shifted

        return base


# ═════════════════════════════════════════════════════════════════════
# Particles
# ═════════════════════════════════════════════════════════════════════
class _Particle:
    __slots__ = ("x", "y", "vx", "vy", "r", "alpha", "life", "max_life")
    def __init__(self, cx: int, cy: int) -> None:
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
# Main GUI
# ═════════════════════════════════════════════════════════════════════
class OverwatchGUI(ctk.CTk):
    """PMC Overwatch — alive avatar that feels like a real co-streamer."""

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("PMC Overwatch")
        self.geometry("440x780")
        self.minsize(400, 700)
        self.configure(fg_color=_BG)
        self.resizable(True, True)

        self.shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._toggle_cb: Optional[Callable[[bool], None]] = None
        self._is_running = False
        self._obs_mode = False

        # Session stats
        self._session_start = datetime.now()
        self._words_spoken = 0
        self._responses = 0

        # Chat history
        self._chat_log: list[str] = []
        Path(_LOG_DIR).mkdir(exist_ok=True)

        # Animation state
        self._mode = "idle"
        self._time = 0.0
        self._photo: Optional[ImageTk.PhotoImage] = None

        avatar_size = min(_CANVAS_W - 40, _CANVAS_H - 60)
        self._engine = _AliveEngine(avatar_size)
        self._avatar_x = (_CANVAS_W - avatar_size) // 2
        self._avatar_y = 6

        # Blink controller
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

        # Input/language
        self._input_mode = os.getenv("INPUT_MODE", "auto").lower()
        self._language = os.getenv("WHISPER_LANGUAGE", "auto").lower()

        # Voice bars
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
        logger.info("Alive avatar GUI initialized (v0.21.0)")

    # ══ HEADER ════════════════════════════════════════════════════════
    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, corner_radius=16, fg_color=_CARD,
                           border_width=1, border_color=_BORDER)
        hdr.pack(fill="x", padx=20, pady=(16, 0))
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=12)
        logo = ctk.CTkFrame(inner, fg_color="transparent")
        logo.pack(side="left")
        _ft = "Segoe UI" if platform.system() == "Windows" else "SF Pro Display"
        ctk.CTkLabel(logo, text="PMC Overwatch",
                     font=ctk.CTkFont(family=_ft, size=22, weight="bold"),
                     text_color=_TEXT).pack(anchor="w")
        ctk.CTkLabel(logo, text="AI Companion  ·  Escape from Tarkov",
                     font=ctk.CTkFont(size=11), text_color=_TEXT2).pack(anchor="w")
        self._btn = ctk.CTkButton(
            inner, text="▶  Start", width=140, height=42, corner_radius=12,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=_GREEN, hover_color=_GREEN_H, text_color="white",
            command=self._on_toggle)
        self._btn.pack(side="right")

    # ══ CANVAS ════════════════════════════════════════════════════════
    def _build_agent(self) -> None:
        self._cv = tk.Canvas(self, width=_CANVAS_W, height=_CANVAS_H,
                             bg=_BG, highlightthickness=0, bd=0)
        self._cv.pack(pady=(4, 0))
        self._av_status = ctk.CTkLabel(
            self, text="OFFLINE",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=_MUTED)
        self._av_status.pack(pady=(0, 4))

    # ══ RENDERING ════════════════════════════════════════════════════
    def _render_frame(self) -> None:
        cv = self._cv
        cv.delete("all")
        glow_c = _GLOW.get(self._mode, _MUTED)
        glow_rgb = _GLOW_RGB.get(self._mode, (61, 68, 80))
        cx = _CANVAS_W // 2
        cy = _CANVAS_H // 2 - 10

        # ── Ambient glow ──────────────────────────────────────────────
        if self._mode != "idle":
            glow_a = 25 + int((math.sin(self._time * 0.5) + 1) * 10)
            r, g, b = glow_rgb
            gc = f"#{max(10, r * glow_a // 255):02x}{max(14, g * glow_a // 255):02x}{max(20, b * glow_a // 255):02x}"
            ar = self._engine._size // 2 + 15
            cv.create_oval(cx - ar, cy - ar, cx + ar, cy + ar, fill=gc, outline="")

        # ── Particles ─────────────────────────────────────────────────
        for p in self._particles:
            if p.alpha > 0.02:
                a = int(min(1.0, p.alpha) * 80)
                pr = max(10, int(glow_rgb[0] * a / 255))
                pg = max(14, int(glow_rgb[1] * a / 255))
                pb = max(20, int(glow_rgb[2] * a / 255))
                pc = f"#{pr:02x}{pg:02x}{pb:02x}"
                cv.create_oval(p.x - p.r, p.y - p.r, p.x + p.r, p.y + p.r,
                               fill=pc, outline="")

        # ── Glow rings ────────────────────────────────────────────────
        if self._mode != "idle":
            ar = self._engine._size // 2
            pulse = (math.sin(self._time * 0.7) + 1) * 0.5
            for i in range(3):
                rr = ar + 8 + i * 10 + int(pulse * 3)
                a = max(0, 45 - i * 16)
                rc = max(10, int(glow_rgb[0] * a / 255))
                gc = max(14, int(glow_rgb[1] * a / 255))
                bc = max(20, int(glow_rgb[2] * a / 255))
                c = f"#{rc:02x}{gc:02x}{bc:02x}"
                cv.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=c, width=2)

        # ── Avatar (rendered with alive motion) ───────────────────────
        frame = self._engine.render(self._time, self._amplitude, self._mode)
        if frame is not None:
            self._photo = ImageTk.PhotoImage(frame)
            cv.create_image(self._avatar_x, self._avatar_y,
                            image=self._photo, anchor="nw")

        # ── Waveform bars ─────────────────────────────────────────────
        total = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        bx = _CANVAS_W // 2 - total // 2
        by = _CANVAS_H - 22
        for i in range(_N_BARS):
            h = max(1, int(self._bar_current[i] * _BAR_MAX_H))
            x = bx + i * (_BAR_W + _BAR_GAP)
            cv.create_rectangle(x, by - h, x + _BAR_W, by, fill=glow_c, outline="")
            cv.create_rectangle(x, by, x + _BAR_W, by + h, fill=glow_c, outline="")

    # ══ ANIMATION LOOP ═══════════════════════════════════════════════
    def _update_state(self, dt: float) -> None:
        mode = self._mode
        eng = self._engine

        # ── Breathing (always active) ─────────────────────────────────
        eng._breath_phase += dt * 1.2

        # ── Micro-expressions (always active) ─────────────────────────
        eng.update_micro(dt)

        # ── Blink controller ─────────────────────────────────────────
        self._blink_timer += dt
        if self._blink_stage == 0:
            if self._blink_timer >= self._blink_cd:
                self._blink_stage = 1
                self._blink_dur = 0.0
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
                    self._double_blink_count = 1
                    self._blink_stage = 0
                    self._blink_cd = random.uniform(0.15, 0.3)
                else:
                    self._blink_stage = 0
                    self._blink_timer = 0.0
                    self._blink_cd = random.uniform(2.0, 5.0)

        # ── Amplitude smoothing ───────────────────────────────────────
        self._amplitude += (self._amplitude_target - self._amplitude) * 0.4

        # ── Mode-specific expression blends ───────────────────────────
        if mode == "speaking":
            amp = self._amplitude
            eng.mouth_blend = min(1.0, amp * 2.0)
            eng.mouth_wide = amp > 0.4
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

        # Emotion override
        if self._emotion == "happy":
            eng.smile_blend = _lerp(eng.smile_blend, 0.9, 0.06)
            self._emotion_timer += dt
            if self._emotion_timer > 3.0:
                self._emotion = "neutral"; self._emotion_timer = 0.0

        # ── Particles ────────────────────────────────────────────────
        self._particle_timer += dt
        if self._particle_timer > 0.25 and mode != "idle" and len(self._particles) < 18:
            self._particle_timer = 0.0
            self._particles.append(_Particle(_CANVAS_W // 2, _CANVAS_H // 2))
        alive = []
        for p in self._particles:
            p.life += dt
            p.x += p.vx; p.y += p.vy
            p.alpha = min(1.0, p.life / 0.5) * max(0.0, 1.0 - (p.life - p.max_life + 1.0))
            if p.life < p.max_life:
                alive.append(p)
        self._particles = alive

        # ── Voice bars (sine wave) ───────────────────────────────────
        for i in range(_N_BARS):
            if mode in ("speaking", "listening"):
                wave = math.sin(self._time * 3.0 + i * 0.4) * 0.3 + 0.5
                self._bar_target[i] = wave * random.uniform(0.3, 1.0)
            else:
                self._bar_target[i] = 0.0
            self._bar_current[i] = _lerp(self._bar_current[i], self._bar_target[i], 0.2)

    def _start_anim(self) -> None:
        self._tick()

    def _tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        dt = 1.0 / _FPS
        self._time += dt
        self._update_state(dt)
        self._render_frame()
        self.after(int(1000 / _FPS), self._tick)

    # ══ LOG PANEL ════════════════════════════════════════════════════
    def _build_log(self) -> None:
        wrapper = ctk.CTkFrame(self, corner_radius=16, fg_color=_CARD,
                               border_width=1, border_color=_BORDER)
        wrapper.pack(fill="both", expand=True, padx=20, pady=(4, 0))
        inner = ctk.CTkFrame(wrapper, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=12, pady=12)

        top_bar = ctk.CTkFrame(inner, fg_color="transparent")
        top_bar.pack(fill="x")
        ctk.CTkLabel(top_bar, text="Activity Log",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=_TEXT2).pack(side="left", anchor="w")

        self._lang_var = ctk.StringVar(value=self._language.upper())
        lang_menu = ctk.CTkOptionMenu(
            top_bar, values=["AUTO", "EN", "RU", "RO"],
            variable=self._lang_var, width=80, height=24,
            font=ctk.CTkFont(size=10), fg_color=_SURFACE,
            button_color=_BORDER, command=self._on_lang_change)
        lang_menu.pack(side="right")
        ctk.CTkLabel(top_bar, text="🌐", font=ctk.CTkFont(size=12),
                     text_color=_TEXT2).pack(side="right", padx=(0, 4))

        self._log = ctk.CTkTextbox(
            inner, font=ctk.CTkFont(family="Consolas", size=11),
            height=120, fg_color=_SURFACE, text_color=_TEXT, corner_radius=8,
            border_width=1, border_color=_BORDER, wrap="word")
        self._log.pack(fill="both", expand=True, pady=(6, 0))
        self._log.configure(state="disabled")

        _chat_in = ctk.CTkFrame(inner, fg_color="transparent")
        _chat_in.pack(fill="x", pady=(8, 0))
        self._chat_entry = ctk.CTkEntry(
            _chat_in, placeholder_text="Type a message…",
            font=ctk.CTkFont(size=12), fg_color=_SURFACE,
            text_color=_TEXT, border_color=_BORDER, corner_radius=10,
            height=36)
        self._chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._chat_entry.bind("<Return>", self._on_chat_send)
        self._chat_btn = ctk.CTkButton(
            _chat_in, text="Send", width=64, height=36, corner_radius=10,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=_CYAN, hover_color="#00b8dd", text_color="black",
            command=self._on_chat_send)
        self._chat_btn.pack(side="right")

    # ══ FOOTER ═══════════════════════════════════════════════════════
    def _build_footer(self) -> None:
        ftr = ctk.CTkFrame(self, corner_radius=12, fg_color=_CARD,
                           border_width=1, border_color=_BORDER, height=36)
        ftr.pack(fill="x", padx=20, pady=(4, 12))
        inner = ctk.CTkFrame(ftr, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=6)
        self._dot = tk.Canvas(inner, width=10, height=10, bg=_CARD,
                              highlightthickness=0, bd=0)
        self._dot.pack(side="left", padx=(0, 8))
        self._dot_id = self._dot.create_oval(1, 1, 9, 9, fill=_MUTED, outline="")
        self._status_lbl = ctk.CTkLabel(inner, text="Offline",
                                        font=ctk.CTkFont(size=12), text_color=_TEXT2)
        self._status_lbl.pack(side="left")
        ctk.CTkLabel(inner, text="Ctrl+O: OBS  Ctrl+P: Persona",
                     font=ctk.CTkFont(size=9), text_color=_MUTED).pack(side="right", padx=(0, 8))
        ctk.CTkLabel(inner, text="v0.21.0", font=ctk.CTkFont(size=11),
                     text_color=_MUTED).pack(side="right", padx=(0, 8))

    # ══ OBS OVERLAY ══════════════════════════════════════════════════
    def _toggle_obs(self) -> None:
        """Standard window overlay — BattlEye safe. No hooks or injection."""
        self._obs_mode = not self._obs_mode
        if self._obs_mode:
            self.overrideredirect(True)
            self.attributes("-topmost", True)
            try:
                self.attributes("-transparentcolor", _BG)
            except Exception:
                pass
            self.log("🎬 OBS Overlay ON (Ctrl+O to disable)")
        else:
            self.overrideredirect(False)
            self.attributes("-topmost", False)
            try:
                self.attributes("-transparentcolor", "")
            except Exception:
                pass
            self.log("🎬 OBS Overlay OFF")

    # ══ PERSONA EDITOR ═══════════════════════════════════════════════
    def _open_persona_editor(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("Persona Editor")
        win.geometry("500x400")
        win.configure(fg_color=_BG)
        win.attributes("-topmost", True)

        ctk.CTkLabel(win, text="🛡️ Persona Editor",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=_TEXT).pack(pady=(16, 8))
        ctk.CTkLabel(win, text="Edit the AI personality and system prompt",
                     font=ctk.CTkFont(size=11), text_color=_TEXT2).pack()

        persona = self._load_persona()
        frame = ctk.CTkFrame(win, fg_color=_CARD, corner_radius=12)
        frame.pack(fill="both", expand=True, padx=20, pady=12)

        ctk.CTkLabel(frame, text="Name:", font=ctk.CTkFont(size=12),
                     text_color=_TEXT2).pack(anchor="w", padx=12, pady=(12, 0))
        name_entry = ctk.CTkEntry(frame, fg_color=_SURFACE, text_color=_TEXT,
                                  border_color=_BORDER, corner_radius=8)
        name_entry.pack(fill="x", padx=12, pady=(4, 8))
        name_entry.insert(0, persona.get("name", "PMC Operator"))

        ctk.CTkLabel(frame, text="System Prompt:", font=ctk.CTkFont(size=12),
                     text_color=_TEXT2).pack(anchor="w", padx=12)
        prompt_box = ctk.CTkTextbox(frame, fg_color=_SURFACE, text_color=_TEXT,
                                    corner_radius=8, height=180, wrap="word")
        prompt_box.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        prompt_box.insert("1.0", persona.get("prompt", ""))

        def save():
            data = {"name": name_entry.get().strip(),
                    "prompt": prompt_box.get("1.0", "end").strip()}
            self._save_persona(data)
            self.log("🛡️ Persona saved")
            win.destroy()

        ctk.CTkButton(win, text="💾 Save", width=120, height=36, corner_radius=10,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      fg_color=_GREEN, hover_color=_GREEN_H, text_color="white",
                      command=save).pack(pady=(0, 16))

    def _load_persona(self) -> dict:
        try:
            if os.path.exists(_PERSONA_FILE):
                with open(_PERSONA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"name": "PMC Operator", "prompt": ""}

    def _save_persona(self, data: dict) -> None:
        try:
            with open(_PERSONA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            logger.exception("Failed to save persona")

    def _on_lang_change(self, value: str) -> None:
        os.environ["WHISPER_LANGUAGE"] = value.lower()
        self._language = value.lower()
        self.log(f"🌐 Language → {value}")

    # ══ PUBLIC API ════════════════════════════════════════════════════
    def set_toggle_callback(self, cb: Callable[[bool], None]) -> None:
        self._toggle_cb = cb
    def register_thread(self, t: threading.Thread) -> None:
        self._threads.append(t)
    def log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}]  {msg}"
        self._chat_log.append(line)
        self.after(0, self._do_log, f"{line}\n")
    def set_status(self, text: str) -> None:
        self.after(0, self._do_status, text)
    def force_toggle_off(self) -> None:
        self.after(0, self._force_off)
    def set_vis_mode(self, mode: str) -> None:
        self.after(0, self._set_mode, mode)
    def set_amplitude(self, amp: float) -> None:
        self._amplitude_target = amp
    def set_emotion(self, emotion: str) -> None:
        self.after(0, self._do_emotion, emotion)
    def add_response_stats(self, word_count: int) -> None:
        self._words_spoken += word_count
        self._responses += 1

    # ══ INTERNAL ═════════════════════════════════════════════════════
    def _do_log(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        labels = {
            "idle": ("OFFLINE", _MUTED), "listening": ("LISTENING", _GREEN),
            "thinking": ("THINKING", _AMBER), "speaking": ("SPEAKING", _CYAN),
        }
        t, c = labels.get(mode, ("OFFLINE", _MUTED))
        self._av_status.configure(text=t, text_color=c)

    def _do_emotion(self, emotion: str) -> None:
        self._emotion = emotion
        self._emotion_timer = 0.0

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
        self._dot.itemconfig(self._dot_id, fill=color)

    def _on_toggle(self) -> None:
        self._is_running = not self._is_running
        if self._is_running:
            self._btn.configure(text="■  Stop", fg_color=_RED, hover_color=_RED_H)
            self._set_mode("listening")
        else:
            self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H)
            self._set_mode("idle")
        if self._toggle_cb:
            self._toggle_cb(self._is_running)

    def _force_off(self) -> None:
        self._is_running = False
        self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H)
        self._set_mode("idle")

    def _on_chat_send(self, event=None) -> None:
        text = self._chat_entry.get().strip()
        if text and self._toggle_cb:
            self._chat_entry.delete(0, "end")
            self.log(f"[You] {text}")
            import threading as _t
            _t.Thread(target=self._toggle_cb, args=(True,), daemon=True).start()

    def _save_chat_history(self) -> None:
        if self._chat_log:
            ts = self._session_start.strftime("%Y%m%d_%H%M%S")
            path = os.path.join(_LOG_DIR, f"session_{ts}.log")
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(self._chat_log))
            except Exception:
                logger.exception("Failed to save chat history")

    def _on_close(self) -> None:
        self.shutdown_event.set()
        self._save_chat_history()
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=2.0)
        try:
            self.destroy()
        except Exception:
            pass


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
        time.sleep(2)  # idle still shows motion!
        app.after(0, app._on_close)
    threading.Thread(target=_demo, daemon=True).start()
    app.mainloop()
    print("ALIVE AVATAR DEMO PASSED")
