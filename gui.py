"""
PMC Overwatch GUI — Sprite-based holographic avatar with alive animation.

v0.25.0:
  • Premium neural voices (Ava Multilingual)
  • Restores the holographic girl sprites the user liked
  • Keeps alive animation engine (SmoothedNoise, micro-expressions, nods)
  • Holographic post-processing on sprites (scanlines, aberration, flicker)
  • Glass-morphism UI
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

from expression_engine import ExpressionEngine, Emotion

logger = logging.getLogger(__name__)

# ── Palette ──────────────────────────────────────────────────────────
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
_HOLO = "#00f0ff"
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

_GLOW = {"idle": _MUTED, "listening": _GREEN, "thinking": _AMBER, "speaking": _HOLO}
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
class _Noise:
    def __init__(self, speed=0.5, amp=1.0):
        self._s, self._a = speed, amp
        self._o = random.uniform(0, 1000)
    def at(self, t):
        p = t * self._s + self._o
        return (math.sin(p) * 0.5 + math.sin(p * 2.3 + 1.7) * 0.3
                + math.sin(p * 4.1 + 3.2) * 0.2) * self._a


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
# Holographic Post-Processing
# ═════════════════════════════════════════════════════════════════════
class _HoloFX:
    """Scanlines + chromatic aberration + flicker on the sprite."""
    def __init__(self, size):
        self._size = size
        self._scan = self._make_lines(size)
        self._fc = 0

    @staticmethod
    def _make_lines(s):
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for y in range(0, s, 3):
            d.line([(0, y), (s, y)], fill=(0, 0, 0, 22), width=1)
        return img

    def apply(self, frame, t, mode):
        self._fc += 1
        w, h = frame.size
        # Chromatic aberration
        r, g, b, a = frame.split()
        rn = Image.new("L", (w, h), 0); rn.paste(r, (1, 0))
        bn = Image.new("L", (w, h), 0); bn.paste(b, (-1, 0))
        frame = Image.merge("RGBA", (rn, g, bn, a))
        # Scanlines
        frame = Image.alpha_composite(frame, self._scan)
        # Glow — subtle translucent halo (reduce alpha, not brightness)
        glow = frame.filter(ImageFilter.GaussianBlur(8))
        gr, gg, gb, ga = glow.split()
        ga = ga.point(lambda p: int(p * 0.15))  # 15% opacity
        glow = Image.merge("RGBA", (gr, gg, gb, ga))
        frame = Image.alpha_composite(frame, glow)
        # Flicker
        flk = 0.96 + math.sin(t * 10.0) * 0.02 + random.uniform(-0.005, 0.005)
        frame = ImageEnhance.Brightness(frame).enhance(max(0.9, min(1.05, flk)))
        # Tint
        if mode != "idle":
            frame = Image.alpha_composite(frame, Image.new("RGBA", (w, h), (0, 180, 255, 10)))
        return frame


# ═════════════════════════════════════════════════════════════════════
# State-Based Sprite Engine
# ═════════════════════════════════════════════════════════════════════
class _AliveEngine:
    """State-based sprite display with organic motion and smooth transitions.

    Sprite system with 15 expression states, driven by the ExpressionEngine.
    Supports per-emotion face selection during idle, speaking, and other modes.
    """
    _SPRITES = {
        # Base states
        "idle": "idle.png",
        "blink": "blink.png",
        "listen": "listen.png",
        "think": "think.png",
        # Speaking — amplitude-driven (neutral emotion)
        "speak_calm": "speak_calm.png",
        "speak_mid": "speak_mid.png",
        "speak_open": "speak_open.png",
        # Expressive faces
        "smile": "smile.png",
        "smile_speak": "smile_speak.png",
        "smirk": "smirk.png",
        "surprise": "surprise.png",
        "concern": "concern.png",
        "excited": "excited.png",
        "confident": "confident.png",
    }

    def __init__(self, size):
        self._size = size
        self._imgs: dict[str, Image.Image] = {}
        self._holo = _HoloFX(size)
        self._load()
        # Expression engine
        self.expression = ExpressionEngine()
        # Motion noise
        self.n_hx = _Noise(0.4, 7.0)
        self.n_hy = _Noise(0.35, 4.0)
        self.n_sc = _Noise(0.6, 0.012)
        self.breath = random.uniform(0, math.pi)
        # State flags
        self.is_blinking = False
        self.mouth_amp = 0.0
        self.prev_amp = 0.0
        # Cross-fade transition (2 frames ≈ 66ms at 30 FPS)
        self._prev_sprite_key = "idle"
        self._curr_sprite_key = "idle"
        self._fade_progress = 1.0   # 1.0 = fully transitioned
        self._FADE_SPEED = 0.5      # fraction per frame (2 frames to complete)

    def _load(self):
        for k, fn in self._SPRITES.items():
            p = os.path.join(_ASSET_DIR, fn)
            if os.path.exists(p):
                try:
                    self._imgs[k] = Image.open(p).convert("RGBA").resize(
                        (self._size, self._size), Image.LANCZOS)
                except Exception:
                    logger.warning("Sprite load failed: %s", p)
        fallback = Image.new("RGBA", (self._size, self._size), (0, 20, 30, 255))
        for k in self._SPRITES:
            if k not in self._imgs:
                self._imgs[k] = fallback

    def _pick_sprite_key(self, mode):
        """Select sprite key via ExpressionEngine + blink override."""
        if self.is_blinking:
            return "blink"
        key = self.expression.get_sprite(mode, self.mouth_amp)
        # Validate the key exists, fall back gracefully
        if key not in self._imgs:
            logger.warning("Sprite '%s' not loaded, falling back to idle", key)
            return "idle"
        return key

    def _get_frame(self, mode):
        """Get the display frame with smooth cross-fade between states."""
        new_key = self._pick_sprite_key(mode)
        # Track state transition
        if new_key != self._curr_sprite_key:
            self._prev_sprite_key = self._curr_sprite_key
            self._curr_sprite_key = new_key
            self._fade_progress = 0.0
        # Advance fade
        if self._fade_progress < 1.0:
            self._fade_progress = min(1.0, self._fade_progress + self._FADE_SPEED)
            if self._fade_progress >= 1.0:
                return self._imgs[self._curr_sprite_key]
            # Cross-fade between previous and current sprite
            return Image.blend(
                self._imgs[self._prev_sprite_key],
                self._imgs[self._curr_sprite_key],
                self._fade_progress,
            )
        return self._imgs[self._curr_sprite_key]

    def render(self, t, amp, mode):
        base = self._get_frame(mode)
        w, h = base.size
        # Breathing
        br = math.sin(self.breath) * 4.0
        bs = 1.0 + math.sin(self.breath * 0.5) * 0.012
        # Head sway
        dx = int(self.n_hx.at(t))
        dy = int(self.n_hy.at(t) + br)
        # Jaw bounce on speaking (amplitude change rate)
        if mode == "speaking":
            dy += int(abs(self.mouth_amp - self.prev_amp) * 8.0)
        if mode == "listening":
            dy += 3
        sc = bs + self.n_sc.at(t)
        nw, nh = int(w * sc), int(h * sc)
        frame = base.copy()
        if nw != w or nh != h:
            frame = frame.resize((nw, nh), Image.LANCZOS)
            cx = max(0, min(nw - w, (nw - w) // 2 - dx))
            cy = max(0, min(nh - h, (nh - h) // 2 - dy))
            frame = frame.crop((cx, cy, cx + w, cy + h))
        elif abs(dx) > 0 or abs(dy) > 0:
            sh = Image.new("RGBA", (w, h), (5, 8, 16, 255))
            sh.paste(frame, (max(-w // 4, min(w // 4, dx)), max(-h // 4, min(h // 4, dy))))
            frame = sh
        return self._holo.apply(frame, t, mode)

# Particles
# ═════════════════════════════════════════════════════════════════════
class _Particle:
    __slots__ = ("x", "y", "vx", "vy", "r", "alpha", "life", "max_life")
    def __init__(self, cx, cy):
        a = random.uniform(0, 2 * math.pi)
        d = random.uniform(80, 170)
        self.x = cx + math.cos(a) * d; self.y = cy + math.sin(a) * d
        self.vx = random.uniform(-0.3, 0.3); self.vy = random.uniform(-0.5, -0.1)
        self.r = random.uniform(1.0, 2.5); self.alpha = 0.0
        self.life = 0.0; self.max_life = random.uniform(3.0, 6.0)


# ═════════════════════════════════════════════════════════════════════
# Main GUI
# ═════════════════════════════════════════════════════════════════════
class OverwatchGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("PMC Overwatch")
        self.geometry("460x820"); self.minsize(420, 740)
        self.configure(fg_color=_BG); self.resizable(True, True)

        self.shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._toggle_cb = None; self._chat_cb = None; self._mic_cb = None; self._is_running = False; self._obs_mode = False
        self._session_start = datetime.now()
        self._words_spoken = 0; self._responses = 0
        self._chat_log: list[str] = []
        Path(_LOG_DIR).mkdir(exist_ok=True)
        self._mode = "idle"; self._time = 0.0
        self._photo = None
        self._expression_engine = None  # set after _engine is built

        sz = min(_CANVAS_W - 40, _CANVAS_H - 60)
        self._engine = _AliveEngine(sz)
        self._expression_engine = self._engine.expression  # expose for main.py
        self._ax = (_CANVAS_W - sz) // 2; self._ay = 6

        self._bt = 0.0; self._bcd = random.uniform(2.0, 5.0)
        self._bs = 0; self._bd = 0.0
        self._db = False; self._dbc = 0
        self._amp = 0.0; self._amp_t = 0.0
        self._emotion = "neutral"; self._et = 0.0
        self._particles: list[_Particle] = []
        self._pt = 0.0
        self._language = os.getenv("WHISPER_LANGUAGE", "auto").lower()
        self._bar_t = [0.0] * _N_BARS; self._bar_c = [0.0] * _N_BARS
        self._mic_map: dict[str, Optional[int]] = {}  # display name → device index

        # Persistent canvas item IDs for flicker-free rendering
        self._cv_glow_id = None
        self._cv_ring_ids: list[int] = []
        self._cv_particle_ids: list[int] = []
        self._cv_img_id = None
        self._cv_bar_ids: list[tuple[int, int]] = []  # (top, bottom) pairs
        self._canvas_ready = False

        self._build_header(); self._build_agent()
        self._build_log(); self._build_footer()
        self._tick_loop()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-o>", lambda e: self._toggle_obs())
        self.bind("<Control-p>", lambda e: self._open_persona_editor())
        logger.info("Sprite holographic GUI initialized (v0.25.0)")

    def _build_header(self):
        hdr = ctk.CTkFrame(self, corner_radius=20, fg_color=_CARD,
                           border_width=1, border_color=_BORDER)
        hdr.pack(fill="x", padx=24, pady=(20, 0))
        inn = ctk.CTkFrame(hdr, fg_color="transparent")
        inn.pack(fill="x", padx=24, pady=14)
        lo = ctk.CTkFrame(inn, fg_color="transparent"); lo.pack(side="left")
        ft = "Segoe UI" if platform.system() == "Windows" else "SF Pro Display"
        ctk.CTkLabel(lo, text="PMC Overwatch",
                     font=ctk.CTkFont(family=ft, size=24, weight="bold"),
                     text_color=_TEXT).pack(anchor="w")
        ctk.CTkLabel(lo, text="AI Voice Companion",
                     font=ctk.CTkFont(family=ft, size=11),
                     text_color=_HOLO).pack(anchor="w")
        self._btn = ctk.CTkButton(
            inn, text="▶  Start", width=140, height=44, corner_radius=14,
            font=ctk.CTkFont(family=ft, size=14, weight="bold"),
            fg_color=_GREEN, hover_color=_GREEN_H, text_color="white",
            command=self._on_toggle)
        self._btn.pack(side="right")

    def _build_agent(self):
        self._cv = tk.Canvas(self, bg=_BG, highlightthickness=0, bd=0)
        self._cv.pack(fill="both", expand=True, pady=(8, 0))
        self._av_st = ctk.CTkLabel(self, text="◆ OFFLINE",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_MUTED)
        self._av_st.pack(pady=(2, 4))

    def _init_canvas_items(self):
        """Create all persistent canvas items once (called on first render)."""
        cv = self._cv
        # Glow oval (behind everything)
        self._cv_glow_id = cv.create_oval(0, 0, 0, 0,
                                          fill=_BG, outline="", state="hidden")
        # 3 ring ovals
        self._cv_ring_ids = []
        for i in range(3):
            rid = cv.create_oval(0, 0, 0, 0, outline=_BG, width=1, state="hidden")
            self._cv_ring_ids.append(rid)
        # Particle ovals (pool of 20)
        self._cv_particle_ids = []
        for _ in range(20):
            pid = cv.create_oval(0, 0, 0, 0, fill=_BG, outline="", state="hidden")
            self._cv_particle_ids.append(pid)
        # Agent sprite image (position updated each frame)
        self._cv_img_id = cv.create_image(0, 0, anchor="nw")
        # Waveform bars
        self._cv_bar_ids = []
        for i in range(_N_BARS):
            top_id = cv.create_rectangle(0, 0, 0, 0, fill=_MUTED, outline="")
            bot_id = cv.create_rectangle(0, 0, 0, 0, fill=_MUTED, outline="")
            self._cv_bar_ids.append((top_id, bot_id))
        self._canvas_ready = True

    def _render(self):
        if not self._canvas_ready:
            self._init_canvas_items()
        cv = self._cv
        # Read actual canvas dimensions for flexible layout
        cw = cv.winfo_width()
        ch = cv.winfo_height()
        if cw < 2 or ch < 2:  # canvas not mapped yet
            return
        gc = _GLOW.get(self._mode, _MUTED)
        gr = _GLOW_RGB.get(self._mode, (48, 54, 61))
        cx, cy = cw // 2, ch // 2 - 10
        sz = self._engine._size
        ar = sz // 2 + 20
        # Position the sprite centred in the canvas
        img_x = (cw - sz) // 2
        img_y = max(6, (ch - sz - 30) // 2)  # leave room for bars at bottom
        # Glow oval
        if self._mode != "idle":
            ga = 20 + int((math.sin(self._time * 0.5) + 1) * 8)
            rr, gg, bb = gr
            c = f"#{max(5, rr * ga // 255):02x}{max(8, gg * ga // 255):02x}{max(16, bb * ga // 255):02x}"
            cv.coords(self._cv_glow_id, cx - ar, cy - ar, cx + ar, cy + ar)
            cv.itemconfig(self._cv_glow_id, fill=c, state="normal")
            for i, rid in enumerate(self._cv_ring_ids):
                r = ar - 5 + i * 6 + int(math.sin(self._time * 0.7 + i) * 2)
                a = max(0, 50 - i * 16)
                rc = max(5, int(rr * a / 255)); gc2 = max(8, int(gg * a / 255)); bc = max(16, int(bb * a / 255))
                cv.coords(rid, cx - r, cy - r, cx + r, cy + r)
                cv.itemconfig(rid, outline=f"#{rc:02x}{gc2:02x}{bc:02x}", state="normal")
        else:
            cv.itemconfig(self._cv_glow_id, state="hidden")
            for rid in self._cv_ring_ids:
                cv.itemconfig(rid, state="hidden")
        # Particles — update pooled ovals (offset relative to canvas centre)
        for idx, pid in enumerate(self._cv_particle_ids):
            if idx < len(self._particles):
                p = self._particles[idx]
                # Remap particle coords: they were spawned around _CANVAS_W/2, _CANVAS_H/2
                px = p.x - _CANVAS_W // 2 + cx
                py = p.y - _CANVAS_H // 2 + cy
                if p.alpha > 0.02:
                    a = int(min(1.0, p.alpha) * 80)
                    fc = f"#{max(5, int(gr[0] * a / 255)):02x}{max(8, int(gr[1] * a / 255)):02x}{max(16, int(gr[2] * a / 255)):02x}"
                    cv.coords(pid, px - p.r, py - p.r, px + p.r, py + p.r)
                    cv.itemconfig(pid, fill=fc, state="normal")
                else:
                    cv.itemconfig(pid, state="hidden")
            else:
                cv.itemconfig(pid, state="hidden")
        # Agent sprite — update in place, centred
        frame = self._engine.render(self._time, self._amp, self._mode)
        if frame:
            self._photo = ImageTk.PhotoImage(frame)
            cv.coords(self._cv_img_id, img_x, img_y)
            cv.itemconfig(self._cv_img_id, image=self._photo)
        # Waveform bars — positioned at bottom of actual canvas
        total = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        bx = cw // 2 - total // 2; by = ch - 20
        for i, (top_id, bot_id) in enumerate(self._cv_bar_ids):
            h = max(1, int(self._bar_c[i] * _BAR_MAX_H))
            x = bx + i * (_BAR_W + _BAR_GAP)
            cv.coords(top_id, x, by - h, x + _BAR_W, by)
            cv.coords(bot_id, x, by, x + _BAR_W, by + h)
            cv.itemconfig(top_id, fill=gc)
            cv.itemconfig(bot_id, fill=gc)

    def _update(self, dt):
        eng = self._engine; m = self._mode
        eng.breath += dt * 1.2
        # ── Blink state machine ──────────────────────────────────
        self._bt += dt
        if self._bs == 0:  # waiting
            eng.is_blinking = False
            if self._bt >= self._bcd:
                self._bs = 1; self._bd = 0.0
                self._db = random.random() < 0.15; self._dbc = 0
        elif self._bs == 1:  # closing
            eng.is_blinking = True
            self._bd += dt
            if self._bd >= 0.06: self._bs = 2; self._bd = 0.0
        elif self._bs == 2:  # closed
            eng.is_blinking = True
            self._bd += dt
            if self._bd >= 0.10: self._bs = 3; self._bd = 0.0
        elif self._bs == 3:  # opening
            eng.is_blinking = False
            self._bd += dt
            if self._bd >= 0.06:
                if self._db and self._dbc == 0:
                    self._dbc = 1; self._bs = 0; self._bcd = random.uniform(0.15, 0.3)
                else:
                    self._bs = 0; self._bt = 0.0; self._bcd = random.uniform(2.0, 5.0)
        # ── Amplitude smoothing ──────────────────────────────────
        if self._amp_t > self._amp:
            self._amp += (self._amp_t - self._amp) * 0.6   # fast attack
        else:
            self._amp += (self._amp_t - self._amp) * 0.15  # slow decay
        eng.prev_amp = eng.mouth_amp
        eng.mouth_amp = min(1.0, self._amp * 2.0) if m == "speaking" else 0.0
        # ── Particles ────────────────────────────────────────────
        self._pt += dt
        if self._pt > 0.2 and m != "idle" and len(self._particles) < 20:
            self._pt = 0.0; self._particles.append(_Particle(_CANVAS_W // 2, _CANVAS_H // 2))
        alive = []
        for p in self._particles:
            p.life += dt; p.x += p.vx; p.y += p.vy
            p.alpha = min(1.0, p.life / 0.5) * max(0.0, 1.0 - (p.life - p.max_life + 1.0))
            if p.life < p.max_life: alive.append(p)
        self._particles = alive
        # ── Waveform bars ────────────────────────────────────────
        for i in range(_N_BARS):
            if m in ("speaking", "listening"):
                w = math.sin(self._time * 3.0 + i * 0.35) * 0.3 + 0.5
                self._bar_t[i] = w * random.uniform(0.3, 1.0)
            else: self._bar_t[i] = 0.0
            self._bar_c[i] = _lerp(self._bar_c[i], self._bar_t[i], 0.18)

    def _tick_loop(self):
        if self.shutdown_event.is_set(): return
        dt = 1.0 / _FPS; self._time += dt
        self._update(dt); self._render()
        self.after(int(1000 / _FPS), self._tick_loop)

    # ══ LOG ══════════════════════════════════════════════════════════
    def _build_log(self):
        wr = ctk.CTkFrame(self, corner_radius=20, fg_color=_CARD, border_width=1, border_color=_BORDER)
        wr.pack(fill="both", expand=True, padx=24, pady=(6, 0))
        inn = ctk.CTkFrame(wr, fg_color="transparent"); inn.pack(fill="both", expand=True, padx=14, pady=14)
        top = ctk.CTkFrame(inn, fg_color="transparent"); top.pack(fill="x")
        ctk.CTkLabel(top, text="◆ Activity Log", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=_TEXT2).pack(side="left")
        self._lv = ctk.StringVar(value=self._language.upper())
        ctk.CTkOptionMenu(top, values=["AUTO", "EN", "RU", "RO"], variable=self._lv, width=80, height=24,
            font=ctk.CTkFont(size=10), fg_color=_GLASS, button_color=_BORDER,
            command=self._on_lang).pack(side="right")
        ctk.CTkLabel(top, text="🌐", font=ctk.CTkFont(size=12), text_color=_HOLO).pack(side="right", padx=(0, 6))
        # ── Mic device selector row ──────────────────────────────────
        mic_row = ctk.CTkFrame(inn, fg_color="transparent"); mic_row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(mic_row, text="🎤", font=ctk.CTkFont(size=12), text_color=_HOLO).pack(side="left", padx=(0, 6))
        self._mic_var = ctk.StringVar(value="Default")
        self._mic_menu = ctk.CTkOptionMenu(
            mic_row, values=["Default"], variable=self._mic_var,
            width=260, height=24, font=ctk.CTkFont(size=10),
            fg_color=_GLASS, button_color=_BORDER, command=self._on_mic)
        self._mic_menu.pack(side="left", fill="x", expand=True)
        self._populate_mic_devices()
        # ─────────────────────────────────────────────────────────────
        self._log_box = ctk.CTkTextbox(inn, font=ctk.CTkFont(family="Cascadia Code, Consolas", size=11),
            height=110, fg_color=_SURFACE, text_color=_TEXT, corner_radius=12,
            border_width=1, border_color=_BORDER, wrap="word")
        self._log_box.pack(fill="both", expand=True, pady=(8, 0)); self._log_box.configure(state="disabled")
        ci = ctk.CTkFrame(inn, fg_color="transparent"); ci.pack(fill="x", pady=(10, 0))
        self._chat_e = ctk.CTkEntry(ci, placeholder_text="Type a message…", font=ctk.CTkFont(size=12),
            fg_color=_GLASS, text_color=_TEXT, border_color=_BORDER, corner_radius=12, height=38)
        self._chat_e.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._chat_e.bind("<Return>", self._on_chat)
        ctk.CTkButton(ci, text="Send", width=70, height=38, corner_radius=12,
            font=ctk.CTkFont(size=12, weight="bold"), fg_color=_ACCENT, hover_color="#388bfd",
            text_color="white", command=self._on_chat).pack(side="right")

    def _build_footer(self):
        ftr = ctk.CTkFrame(self, corner_radius=14, fg_color=_CARD, border_width=1, border_color=_BORDER, height=40)
        ftr.pack(fill="x", padx=24, pady=(6, 16))
        inn = ctk.CTkFrame(ftr, fg_color="transparent"); inn.pack(fill="x", padx=14, pady=8)
        self._dot = tk.Canvas(inn, width=10, height=10, bg=_CARD, highlightthickness=0, bd=0)
        self._dot.pack(side="left", padx=(0, 8))
        self._dot_id = self._dot.create_oval(1, 1, 9, 9, fill=_MUTED, outline="")
        self._st_lbl = ctk.CTkLabel(inn, text="Offline", font=ctk.CTkFont(size=12), text_color=_TEXT2)
        self._st_lbl.pack(side="left")
        ctk.CTkLabel(inn, text="Ctrl+O  Ctrl+P", font=ctk.CTkFont(size=9), text_color=_MUTED).pack(side="right", padx=(0, 10))
        ctk.CTkLabel(inn, text="v0.25.0", font=ctk.CTkFont(size=10), text_color=_MUTED).pack(side="right", padx=(0, 10))

    # ══ OBS / PERSONA ════════════════════════════════════════════════
    def _toggle_obs(self):
        self._obs_mode = not self._obs_mode
        if self._obs_mode:
            self.overrideredirect(True); self.attributes("-topmost", True)
            try: self.attributes("-transparentcolor", _BG)
            except Exception: pass
            self.log("🎬 OBS Overlay ON")
        else:
            self.overrideredirect(False); self.attributes("-topmost", False)
            try: self.attributes("-transparentcolor", "")
            except Exception: pass
            self.log("🎬 OBS Overlay OFF")

    def _open_persona_editor(self):
        win = ctk.CTkToplevel(self); win.title("Persona Editor")
        win.geometry("520x420"); win.configure(fg_color=_BG); win.attributes("-topmost", True)
        ctk.CTkLabel(win, text="🛡️ Persona Editor", font=ctk.CTkFont(size=20, weight="bold"), text_color=_TEXT).pack(pady=(20, 8))
        ctk.CTkLabel(win, text="Edit AI personality", font=ctk.CTkFont(size=11), text_color=_TEXT2).pack()
        p = self._load_persona()
        fr = ctk.CTkFrame(win, fg_color=_CARD, corner_radius=16); fr.pack(fill="both", expand=True, padx=24, pady=16)
        ctk.CTkLabel(fr, text="Name:", text_color=_TEXT2).pack(anchor="w", padx=16, pady=(16, 0))
        ne = ctk.CTkEntry(fr, fg_color=_GLASS, text_color=_TEXT, border_color=_BORDER, corner_radius=10)
        ne.pack(fill="x", padx=16, pady=(4, 10)); ne.insert(0, p.get("name", "PMC Operator"))
        ctk.CTkLabel(fr, text="System Prompt:", text_color=_TEXT2).pack(anchor="w", padx=16)
        pb = ctk.CTkTextbox(fr, fg_color=_GLASS, text_color=_TEXT, corner_radius=10, height=180, wrap="word")
        pb.pack(fill="both", expand=True, padx=16, pady=(4, 16)); pb.insert("1.0", p.get("prompt", ""))
        def save():
            self._save_persona({"name": ne.get().strip(), "prompt": pb.get("1.0", "end").strip()})
            self.log("🛡️ Persona saved"); win.destroy()
        ctk.CTkButton(win, text="💾 Save", width=130, height=38, corner_radius=12,
            fg_color=_GREEN, hover_color=_GREEN_H, text_color="white", command=save).pack(pady=(0, 20))

    def _load_persona(self):
        try:
            if os.path.exists(_PERSONA_FILE):
                with open(_PERSONA_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: pass
        return {"name": "PMC Operator", "prompt": ""}
    def _save_persona(self, d):
        try:
            with open(_PERSONA_FILE, "w", encoding="utf-8") as f: json.dump(d, f, indent=2, ensure_ascii=False)
        except Exception: pass
    def _on_lang(self, v):
        os.environ["WHISPER_LANGUAGE"] = v.lower(); self._language = v.lower()
        self.log(f"🌐 Language → {v}")

    def _populate_mic_devices(self):
        """Query available input devices and populate the mic dropdown."""
        try:
            import sounddevice as _sd
            devices = _sd.query_devices()
            names = ["Default"]
            self._mic_map = {"Default": None}
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    name = f"{d['name']}"
                    # Ensure unique display names
                    if name in self._mic_map:
                        name = f"{d['name']} (#{i})"
                    names.append(name)
                    self._mic_map[name] = i
            self._mic_menu.configure(values=names)
        except Exception:
            logger.warning("Could not enumerate audio devices")

    def _on_mic(self, v):
        """Handle mic device selection change."""
        device_index = self._mic_map.get(v)
        self.log(f"🎤 Mic → {v}")
        if self._mic_cb:
            threading.Thread(target=self._mic_cb, args=(device_index,), name="MicSwitch", daemon=True).start()

    # ══ PUBLIC API ════════════════════════════════════════════════════
    def set_toggle_callback(self, cb): self._toggle_cb = cb
    def set_chat_callback(self, cb): self._chat_cb = cb
    def set_mic_callback(self, cb): self._mic_cb = cb
    def register_thread(self, t): self._threads.append(t)
    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S"); line = f"[{ts}]  {msg}"
        self._chat_log.append(line); self.after(0, self._log_add, f"{line}\n")
    def set_status(self, text): self.after(0, self._do_status, text)
    def force_toggle_off(self): self.after(0, self._force_off)
    def set_vis_mode(self, mode): self.after(0, self._set_mode, mode)
    def set_amplitude(self, amp): self._amp_t = amp
    def set_emotion(self, emotion): self.after(0, self._do_emo, emotion)
    def set_expression(self, emotion_enum):
        """Set expression via Emotion enum (enterprise expression engine)."""
        if self._expression_engine:
            self._expression_engine.set_emotion(emotion_enum)
    def reset_expression(self):
        """Reset expression to neutral."""
        if self._expression_engine:
            self._expression_engine.reset()
    def add_response_stats(self, wc): self._words_spoken += wc; self._responses += 1

    def _log_add(self, l):
        self._log_box.configure(state="normal"); self._log_box.insert("end", l)
        self._log_box.see("end"); self._log_box.configure(state="disabled")
    def _set_mode(self, m):
        self._mode = m
        lbl = {"idle": ("◆ OFFLINE", _MUTED), "listening": ("◆ LISTENING", _GREEN),
               "thinking": ("◆ THINKING", _AMBER), "speaking": ("◆ SPEAKING", _HOLO)}
        t, c = lbl.get(m, ("◆ OFFLINE", _MUTED))
        self._av_st.configure(text=t, text_color=c)
    def _do_emo(self, e): self._emotion = e; self._et = 0.0
    def _do_status(self, text):
        self._st_lbl.configure(text=text); lo = text.lower()
        if "listening" in lo: self._dot.itemconfig(self._dot_id, fill=_GREEN); self._set_mode("listening")
        elif "speaking" in lo: self._dot.itemconfig(self._dot_id, fill=_HOLO); self._set_mode("speaking")
        elif "thinking" in lo: self._dot.itemconfig(self._dot_id, fill=_AMBER); self._set_mode("thinking")
        elif "offline" in lo: self._dot.itemconfig(self._dot_id, fill=_MUTED); self._set_mode("idle")
    def _on_toggle(self):
        self._is_running = not self._is_running
        if self._is_running:
            self._btn.configure(text="■  Stop", fg_color=_RED, hover_color=_RED_H); self._set_mode("listening")
        else:
            self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H); self._set_mode("idle")
        if self._toggle_cb: self._toggle_cb(self._is_running)
    def _force_off(self):
        self._is_running = False
        self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H); self._set_mode("idle")
    def _on_chat(self, event=None):
        text = self._chat_e.get().strip()
        if not text:
            return
        self._chat_e.delete(0, "end")
        self.log(f"[You] {text}")
        if self._chat_cb:
            threading.Thread(target=self._chat_cb, args=(text,), daemon=True).start()
    def _save_hist(self):
        if self._chat_log:
            try:
                with open(os.path.join(_LOG_DIR, f"session_{self._session_start.strftime('%Y%m%d_%H%M%S')}.log"),
                          "w", encoding="utf-8") as f: f.write("\n".join(self._chat_log))
            except Exception: pass
    def _on_close(self):
        self.shutdown_event.set(); self._save_hist()
        for t in self._threads:
            if t.is_alive(): t.join(timeout=2.0)
        try: self.destroy()
        except Exception: pass


if __name__ == "__main__":
    app = OverwatchGUI()
    import time, random as _r
    def _demo():
        time.sleep(1.5); app.after(0, lambda: app.set_vis_mode("listening"))
        time.sleep(2); app.after(0, lambda: app.set_vis_mode("speaking"))
        for _ in range(50): app.set_amplitude(_r.uniform(0.0, 1.0)); time.sleep(0.05)
        app.set_amplitude(0.0); time.sleep(0.5)
        app.after(0, lambda: app.set_emotion("happy")); time.sleep(2)
        app.after(0, lambda: app.set_vis_mode("thinking")); time.sleep(2)
        app.after(0, lambda: app.set_vis_mode("idle")); time.sleep(2)
        app.after(0, app._on_close)
    threading.Thread(target=_demo, daemon=True).start()
    app.mainloop()
    print("SPRITE HOLO V24 DEMO PASSED")
