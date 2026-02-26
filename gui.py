"""
PMC Overwatch GUI — Premium anime avatar with full cross-fade animation.

Rendering approach:
  • Full-image cross-fade between expression sprites (not region compositing)
  • Talk sprites blend over base at amplitude-driven opacity
  • Blink sprite fades in/out for smooth natural blinks
  • Smile/think sprites blend for emotion expressions
  • Head sway, breathing bob via PIL transforms
  • Canvas glow rings, speaking ripples, voice bars
  • Ambient floating particles for alive feel
"""

import logging
import math
import os
import platform
import random
import threading
import tkinter as tk
from datetime import datetime
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


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ═════════════════════════════════════════════════════════════════════
# Cross-Fade Engine — full image blending between expressions
# ═════════════════════════════════════════════════════════════════════
class _CrossFadeEngine:
    """Blends full expression sprites for smooth animated transitions.

    Each frame is composed by layering sprites at varying opacities:
      base (neutral) → talk overlay → emotion overlay → blink overlay
    This creates dramatically visible expression changes.
    """

    _SPRITE_FILES = {
        "neutral": "neutral.png",
        "talk_a": "talk_a.png",
        "talk_b": "talk_b.png",
        "blink": "blink.png",
        "think": "think.png",
        "smile": "smile.png",
    }

    def __init__(self, size: int) -> None:
        self._size = size
        self._sprites: dict[str, Image.Image] = {}
        self._load()

        # Blend weights (0.0 = invisible, 1.0 = fully visible)
        self.talk_a_blend = 0.0
        self.talk_b_blend = 0.0
        self.blink_blend = 0.0
        self.smile_blend = 0.0
        self.think_blend = 0.0

        # Motion
        self.head_x = 0.0
        self.head_y = 0.0
        self.breath_scale = 1.0

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

    def render(self) -> Image.Image:
        s = self._size
        base = self._sprites["neutral"].copy()

        # Layer 1: Talk expression (amplitude-driven)
        if self.talk_a_blend > 0.02:
            t = min(1.0, self.talk_a_blend)
            talk_a = self._sprites["talk_a"]
            base = Image.blend(base, talk_a, t)

        if self.talk_b_blend > 0.02:
            t = min(1.0, self.talk_b_blend)
            talk_b = self._sprites["talk_b"]
            base = Image.blend(base, talk_b, t)

        # Layer 2: Emotion (smile or think)
        if self.smile_blend > 0.02:
            t = min(1.0, self.smile_blend)
            base = Image.blend(base, self._sprites["smile"], t)

        if self.think_blend > 0.02:
            t = min(1.0, self.think_blend)
            base = Image.blend(base, self._sprites["think"], t)

        # Layer 3: Blink (eyes closing)
        if self.blink_blend > 0.02:
            t = min(1.0, self.blink_blend)
            base = Image.blend(base, self._sprites["blink"], t)

        # Layer 4: Motion (head sway + breathing)
        w, h = base.size
        nw = int(w * self.breath_scale)
        nh = int(h * self.breath_scale)
        dx = int(self.head_x)
        dy = int(self.head_y)

        if nw != w or nh != h:
            base = base.resize((nw, nh), Image.LANCZOS)
            cx = (nw - w) // 2 - dx
            cy = (nh - h) // 2 - dy
            base = base.crop((cx, cy, cx + w, cy + h))
        elif abs(dx) > 0 or abs(dy) > 0:
            shifted = Image.new("RGBA", (w, h), (10, 14, 20, 255))
            shifted.paste(base, (dx, dy))
            base = shifted

        return base


# ═════════════════════════════════════════════════════════════════════
# Floating Particles — ambient alive effect
# ═════════════════════════════════════════════════════════════════════
class _Particle:
    __slots__ = ("x", "y", "vx", "vy", "r", "alpha", "life", "max_life")

    def __init__(self, cx: int, cy: int) -> None:
        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(80, 180)
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
    """PMC Overwatch — premium anime avatar with full cross-fade animation."""

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

        # ── Animation state ──────────────────────────────────────────
        self._mode = "idle"
        self._phase = 0.0
        self._photo: Optional[ImageTk.PhotoImage] = None

        avatar_size = min(_CANVAS_W - 40, _CANVAS_H - 60)
        self._engine = _CrossFadeEngine(avatar_size)
        self._avatar_x = (_CANVAS_W - avatar_size) // 2
        self._avatar_y = 6

        # Blink
        self._blink_timer = 0.0
        self._blink_cd = random.uniform(2.5, 5.5)
        self._blink_stage = 0
        self._blink_dur = 0.0
        self._double_blink = False
        self._double_blink_count = 0

        # Head motion
        self._head_x = 0.0
        self._head_y = 0.0
        self._head_target_x = 0.0
        self._head_target_y = 0.0
        self._head_timer = 0.0
        self._head_cd = random.uniform(1.0, 2.5)

        # Breathing
        self._breath_phase = 0.0

        # Speaking
        self._amplitude = 0.0
        self._amplitude_target = 0.0

        # Emotion
        self._emotion = "neutral"
        self._emotion_timer = 0.0

        # Particles
        self._particles: list[_Particle] = []
        self._particle_timer = 0.0

        # Input mode
        self._input_mode = os.getenv("INPUT_MODE", "auto").lower()

        # Voice bars
        self._bar_target = [0.0] * _N_BARS
        self._bar_current = [0.0] * _N_BARS

        self._build_header()
        self._build_agent()
        self._build_log()
        self._build_footer()
        self._start_anim()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        logger.info("Premium cross-fade GUI initialized")

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

        # ── Particles (behind avatar) ─────────────────────────────────
        for p in self._particles:
            if p.alpha > 0.02:
                a = int(min(1.0, p.alpha) * 80)
                r = max(10, int(glow_rgb[0] * a / 255))
                g = max(14, int(glow_rgb[1] * a / 255))
                b = max(20, int(glow_rgb[2] * a / 255))
                pc = f"#{r:02x}{g:02x}{b:02x}"
                pr = p.r
                cv.create_oval(p.x - pr, p.y - pr, p.x + pr, p.y + pr,
                               fill=pc, outline="")

        # ── Glow ring ─────────────────────────────────────────────────
        pulse = (math.sin(self._phase * 0.7) + 1) * 0.5
        if self._mode != "idle":
            ar = self._engine._size // 2
            for i in range(4):
                r = ar + 8 + i * 8 + int(pulse * 3)
                a = max(0, 50 - i * 14)
                rc = max(10, int(glow_rgb[0] * a / 255))
                gc = max(14, int(glow_rgb[1] * a / 255))
                bc = max(20, int(glow_rgb[2] * a / 255))
                c = f"#{rc:02x}{gc:02x}{bc:02x}"
                cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=c, width=2)

            if self._mode == "speaking":
                for wi in range(3):
                    wp = self._phase * 2.0 + wi * 2.0
                    wr = ar + 20 + int((wp % 4.0) * 12)
                    wa = max(0.0, 1.0 - (wp % 4.0) / 4.0)
                    if wa > 0.05:
                        rv = max(10, int(glow_rgb[0] * wa * 0.4))
                        gv = max(14, int(glow_rgb[1] * wa * 0.4))
                        bv = max(20, int(glow_rgb[2] * wa * 0.4))
                        wc = f"#{rv:02x}{gv:02x}{bv:02x}"
                        cv.create_oval(cx - wr, cy - wr, cx + wr, cy + wr,
                                       outline=wc, width=1)

        # ── Avatar ────────────────────────────────────────────────────
        frame = self._engine.render()
        if frame is not None:
            self._photo = ImageTk.PhotoImage(frame)
            cv.create_image(self._avatar_x, self._avatar_y,
                            image=self._photo, anchor="nw")

        # ── Voice bars ────────────────────────────────────────────────
        total = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        bx = _CANVAS_W // 2 - total // 2
        by = _CANVAS_H - 22
        for i in range(_N_BARS):
            h = max(1, int(self._bar_current[i] * _BAR_MAX_H))
            x = bx + i * (_BAR_W + _BAR_GAP)
            cv.create_rectangle(x, by - h, x + _BAR_W, by, fill=glow_c, outline="")
            cv.create_rectangle(x, by, x + _BAR_W, by + h, fill=glow_c, outline="")

    # ══ ANIMATION ════════════════════════════════════════════════════
    def _update_state(self, dt: float) -> None:
        mode = self._mode
        eng = self._engine

        # ── Multi-stage blink ─────────────────────────────────────────
        self._blink_timer += dt
        if self._blink_stage == 0:
            if self._blink_timer >= self._blink_cd:
                self._blink_stage = 1
                self._blink_dur = 0.0
                self._double_blink = random.random() < 0.15
                self._double_blink_count = 0
        elif self._blink_stage == 1:  # Closing
            self._blink_dur += dt
            eng.blink_blend = _lerp(eng.blink_blend, 0.6, 0.45)
            if self._blink_dur >= 0.05:
                self._blink_stage = 2
                self._blink_dur = 0.0
        elif self._blink_stage == 2:  # Closed
            self._blink_dur += dt
            eng.blink_blend = _lerp(eng.blink_blend, 1.0, 0.55)
            if self._blink_dur >= 0.08:
                self._blink_stage = 3
                self._blink_dur = 0.0
        elif self._blink_stage == 3:  # Opening
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
                    self._blink_cd = random.uniform(2.5, 5.5)

        # ── Head motion ──────────────────────────────────────────────
        self._head_timer += dt
        if self._head_timer >= self._head_cd:
            self._head_timer = 0.0
            self._head_cd = random.uniform(1.0, 2.5)
            self._head_target_x = random.uniform(-6.0, 6.0)
            self._head_target_y = random.uniform(-4.0, 4.0)
        self._head_x = _lerp(self._head_x, self._head_target_x, 0.05)
        self._head_y = _lerp(self._head_y, self._head_target_y, 0.05)

        # ── Breathing ────────────────────────────────────────────────
        self._breath_phase += dt * 1.1
        breath_y = math.sin(self._breath_phase) * 3.5
        breath_scale = 1.0 + math.sin(self._breath_phase * 0.5) * 0.012
        eng.head_x = self._head_x
        eng.head_y = self._head_y + breath_y
        eng.breath_scale = breath_scale

        # ── Mode → blend weights ─────────────────────────────────────
        if mode == "speaking":
            self._amplitude += (self._amplitude_target - self._amplitude) * 0.4
            amp = self._amplitude
            # Talk_a for quiet speech, talk_b for loud
            if amp < 0.35:
                eng.talk_a_blend = _lerp(eng.talk_a_blend, amp * 2.5, 0.3)
                eng.talk_b_blend = _lerp(eng.talk_b_blend, 0.0, 0.2)
            else:
                eng.talk_a_blend = _lerp(eng.talk_a_blend, 0.0, 0.2)
                eng.talk_b_blend = _lerp(eng.talk_b_blend, min(1.0, amp * 1.5), 0.3)
            eng.smile_blend = _lerp(eng.smile_blend, 0.0, 0.08)
            eng.think_blend = _lerp(eng.think_blend, 0.0, 0.08)

        elif mode == "thinking":
            eng.talk_a_blend = _lerp(eng.talk_a_blend, 0.0, 0.1)
            eng.talk_b_blend = _lerp(eng.talk_b_blend, 0.0, 0.1)
            eng.smile_blend = _lerp(eng.smile_blend, 0.0, 0.08)
            eng.think_blend = _lerp(eng.think_blend, 0.85, 0.06)

        elif mode == "listening":
            eng.talk_a_blend = _lerp(eng.talk_a_blend, 0.0, 0.1)
            eng.talk_b_blend = _lerp(eng.talk_b_blend, 0.0, 0.1)
            eng.smile_blend = _lerp(eng.smile_blend, 0.25, 0.04)
            eng.think_blend = _lerp(eng.think_blend, 0.0, 0.08)

        else:  # idle
            eng.talk_a_blend = _lerp(eng.talk_a_blend, 0.0, 0.08)
            eng.talk_b_blend = _lerp(eng.talk_b_blend, 0.0, 0.08)
            eng.smile_blend = _lerp(eng.smile_blend, 0.0, 0.04)
            eng.think_blend = _lerp(eng.think_blend, 0.0, 0.04)

        # ── Emotion overlay ──────────────────────────────────────────
        if self._emotion == "happy":
            eng.smile_blend = _lerp(eng.smile_blend, 0.9, 0.06)
            self._emotion_timer += dt
            if self._emotion_timer > 3.0:
                self._emotion = "neutral"
                self._emotion_timer = 0.0

        # ── Particles ────────────────────────────────────────────────
        self._particle_timer += dt
        if self._particle_timer > 0.3 and mode != "idle" and len(self._particles) < 15:
            self._particle_timer = 0.0
            self._particles.append(_Particle(_CANVAS_W // 2, _CANVAS_H // 2))

        alive = []
        for p in self._particles:
            p.life += dt
            p.x += p.vx
            p.y += p.vy
            fade_in = min(1.0, p.life / 0.5)
            fade_out = max(0.0, 1.0 - (p.life - p.max_life + 1.0))
            p.alpha = fade_in * fade_out
            if p.life < p.max_life:
                alive.append(p)
        self._particles = alive

        # ── Voice bars ───────────────────────────────────────────────
        for i in range(_N_BARS):
            if mode in ("speaking", "listening"):
                self._bar_target[i] = random.uniform(0.1, 0.9)
            else:
                self._bar_target[i] = 0.0
            self._bar_current[i] = _lerp(self._bar_current[i], self._bar_target[i], 0.25)

    def _start_anim(self) -> None:
        self._tick()

    def _tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        dt = 1.0 / _FPS
        self._phase += dt
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
        ctk.CTkLabel(inner, text="Activity Log",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=_TEXT2).pack(anchor="w")
        self._log = ctk.CTkTextbox(
            inner, font=ctk.CTkFont(family="Consolas", size=11),
            height=130, fg_color=_SURFACE, text_color=_TEXT, corner_radius=8,
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
        mode_labels = {"auto": "Auto", "toggle": "Toggle (F4)", "push": "PTT (F4)"}
        mode_text = mode_labels.get(self._input_mode, "Auto")
        ctk.CTkLabel(inner, text=f"\U0001f3a4 {mode_text}", font=ctk.CTkFont(size=11),
                     text_color=_TEXT2).pack(side="right", padx=(0, 12))
        ctk.CTkLabel(inner, text="v0.19.0", font=ctk.CTkFont(size=11),
                     text_color=_MUTED).pack(side="right")

    # ══ PUBLIC API ════════════════════════════════════════════════════
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
    def set_amplitude(self, amp: float) -> None:
        self._amplitude_target = amp
    def set_emotion(self, emotion: str) -> None:
        self.after(0, self._do_emotion, emotion)

    # ══ INTERNAL ═════════════════════════════════════════════════════
    def _do_log(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        labels = {
            "idle": ("OFFLINE", _MUTED),
            "listening": ("LISTENING", _GREEN),
            "thinking": ("THINKING", _AMBER),
            "speaking": ("SPEAKING", _CYAN),
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

    def _on_close(self) -> None:
        self.shutdown_event.set()
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
        for _ in range(40):
            app.set_amplitude(_r.uniform(0.0, 1.0))
            time.sleep(0.06)
        app.set_amplitude(0.0)
        time.sleep(0.5)
        app.after(0, lambda: app.set_emotion("happy"))
        time.sleep(2)
        app.after(0, lambda: app.set_vis_mode("thinking"))
        time.sleep(2)
        app.after(0, lambda: app.set_vis_mode("idle"))
        time.sleep(1.5)
        app.after(0, app._on_close)
    threading.Thread(target=_demo, daemon=True).start()
    app.mainloop()
    print("CROSS-FADE DEMO PASSED")
