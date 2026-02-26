"""
PMC Overwatch GUI — Alive anime avatar with region compositing.

Mirror-like animation quality:
  • Region-based compositing (mouth/eye regions blend independently)
  • Random micro head sway and gentle rotation
  • Multi-stage blinks (half→full→half→open)
  • Micro-expressions (occasional smile flickers, eyebrow shifts)
  • Natural speaking with varied cadence and emphasis pauses
  • Breathing with subtle head bob and scale pulse
  • Idle gaze wander (slight eye region shifts)
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
_FPS = 24

_N_BARS = 31
_BAR_W = 3
_BAR_GAP = 1
_BAR_MAX_H = 14

_GLOW = {"idle": _MUTED, "listening": _GREEN, "thinking": _AMBER, "speaking": _CYAN}
_GLOW_RGB = {
    "idle": (61, 68, 80), "listening": (0, 210, 106),
    "thinking": (255, 165, 2), "speaking": (0, 210, 255),
}

# Region definitions (fraction of image height)
_MOUTH_TOP = 0.58
_MOUTH_BOTTOM = 1.0
_MOUTH_FADE = 0.07
_EYE_TOP = 0.20
_EYE_BOTTOM = 0.46
_EYE_FADE = 0.06

_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _ease(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _build_region_mask(w: int, h: int, top: float, bot: float, fade: float) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    px = mask.load()
    tp = int(top * h)
    bp = int(bot * h)
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
# Alive Sprite Engine
# ═════════════════════════════════════════════════════════════════════
class _AliveEngine:
    """Region-composited avatar with micro-expressions and head motion."""

    _SPRITE_FILES = {
        "neutral": "neutral.png",
        "talk_a": "talk_a.png",
        "talk_b": "talk_b.png",
        "blink": "blink.png",
        "think": "think.png",
        "listen": "listen.png",
        "smile": "smile.png",
        "avatar": "avatar.png",
    }

    def __init__(self, cw: int, ch: int) -> None:
        self._sprites: dict[str, Image.Image] = {}
        self._size = min(cw - 40, ch - 60)
        self.avatar_x = (cw - self._size) // 2
        self.avatar_y = 10
        self._mouth_mask: Optional[Image.Image] = None
        self._eye_mask: Optional[Image.Image] = None

        # Compositing state
        self._base = "neutral"
        self._base_from = "neutral"
        self._base_blend = 1.0
        self._base_blend_target = 1.0

        self._mouth_src = "neutral"
        self._mouth_blend = 0.0
        self._mouth_target = 0.0

        self._eye_src = "blink"
        self._eye_blend = 0.0
        self._eye_target = 0.0

        # Head micro-motion
        self._head_x = 0.0
        self._head_y = 0.0
        self._head_target_x = 0.0
        self._head_target_y = 0.0
        self._head_timer = 0.0
        self._head_cd = random.uniform(1.5, 3.5)

        # Breathing
        self._breath_phase = 0.0

        # Micro-expression (occasional smile flicker)
        self._micro_timer = 0.0
        self._micro_cd = random.uniform(6.0, 15.0)
        self._micro_active = False
        self._micro_dur = 0.0

        self._load()

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

        if "neutral" not in self._sprites and self._sprites:
            self._sprites["neutral"] = next(iter(self._sprites.values()))

        for fallback in ("listen", "smile"):
            if fallback not in self._sprites and "neutral" in self._sprites:
                self._sprites[fallback] = self._sprites["neutral"]

        if self._sprites:
            s = self._size
            self._mouth_mask = _build_region_mask(s, s, _MOUTH_TOP, _MOUTH_BOTTOM, _MOUTH_FADE)
            self._eye_mask = _build_region_mask(s, s, _EYE_TOP, _EYE_BOTTOM, _EYE_FADE)

        logger.info("Alive engine: %d sprites loaded", len(self._sprites))

    # ── Mode control ──────────────────────────────────────────────────
    def set_mode(self, mode: str, mouth: Optional[str] = None) -> None:
        if mode == "speaking" and mouth:
            self._mouth_src = mouth if mouth in self._sprites else "talk_a"
            self._mouth_target = 1.0
            self._transition_base("neutral")
        elif mode == "thinking":
            self._mouth_target = 0.0
            self._transition_base("think")
        elif mode == "listening":
            self._mouth_target = 0.0
            self._transition_base("listen")
        else:
            self._mouth_target = 0.0
            self._transition_base("neutral")

    def _transition_base(self, target: str) -> None:
        if target in self._sprites and self._base != target:
            self._base_from = self._base
            self._base = target
            self._base_blend = 0.0
            self._base_blend_target = 1.0

    def set_blink(self, phase: str) -> None:
        """phase: 'close', 'half', 'open'"""
        if phase == "close":
            self._eye_target = 1.0
        elif phase == "half":
            self._eye_target = 0.55
        else:
            self._eye_target = 0.0

    # ── Update ────────────────────────────────────────────────────────
    def update(self, dt: float) -> None:
        # Blend interpolations
        self._mouth_blend = _lerp(self._mouth_blend, self._mouth_target, 0.35)
        if abs(self._mouth_blend - self._mouth_target) < 0.01:
            self._mouth_blend = self._mouth_target

        self._eye_blend = _lerp(self._eye_blend, self._eye_target, 0.45)
        if abs(self._eye_blend - self._eye_target) < 0.01:
            self._eye_blend = self._eye_target

        self._base_blend = _lerp(self._base_blend, self._base_blend_target, 0.1)
        if abs(self._base_blend - self._base_blend_target) < 0.01:
            self._base_blend = self._base_blend_target

        # Head micro-motion (random wander)
        self._head_timer += dt
        if self._head_timer >= self._head_cd:
            self._head_timer = 0.0
            self._head_cd = random.uniform(1.5, 4.0)
            self._head_target_x = random.uniform(-2.5, 2.5)
            self._head_target_y = random.uniform(-1.5, 1.5)
        self._head_x = _lerp(self._head_x, self._head_target_x, 0.04)
        self._head_y = _lerp(self._head_y, self._head_target_y, 0.04)

        # Breathing
        self._breath_phase += dt * 1.1

        # Micro-expressions
        self._micro_timer += dt
        if self._micro_active:
            self._micro_dur += dt
            if self._micro_dur > 0.8:
                self._micro_active = False
                self._micro_dur = 0.0
                self._micro_timer = 0.0
                self._micro_cd = random.uniform(8.0, 20.0)
        elif self._micro_timer >= self._micro_cd:
            self._micro_active = True
            self._micro_dur = 0.0

    # ── Render ────────────────────────────────────────────────────────
    def render(self) -> Optional[Image.Image]:
        if not self._sprites:
            return None

        # Base face (mode transition)
        base_img = self._sprites.get(self._base, self._sprites.get("neutral"))
        if base_img is None:
            return None

        if self._base_blend < 0.99 and self._base_from in self._sprites:
            t = _ease(self._base_blend)
            frame = Image.blend(self._sprites[self._base_from], base_img, t)
        else:
            frame = base_img.copy()

        # Micro-expression overlay (subtle smile flicker)
        if self._micro_active and "smile" in self._sprites and self._mouth_target < 0.5:
            smile_img = self._sprites["smile"]
            micro_t = math.sin(self._micro_dur * math.pi / 0.8) * 0.3
            if micro_t > 0.02 and self._mouth_mask:
                sm = self._mouth_mask.point(lambda p: int(p * micro_t))
                frame.paste(smile_img, mask=sm)

        # Mouth region compositing
        if self._mouth_blend > 0.01 and self._mouth_mask:
            msrc = self._sprites.get(self._mouth_src)
            if msrc:
                sm = self._mouth_mask.point(lambda p: int(p * self._mouth_blend))
                frame.paste(msrc, mask=sm)

        # Eye region compositing
        if self._eye_blend > 0.01 and self._eye_mask:
            esrc = self._sprites.get(self._eye_src, self._sprites.get("blink"))
            if esrc:
                sm = self._eye_mask.point(lambda p: int(p * self._eye_blend))
                frame.paste(esrc, mask=sm)

        # Head motion + breathing
        breath_y = math.sin(self._breath_phase) * 1.5
        breath_scale = 1.0 + math.sin(self._breath_phase * 0.5) * 0.003
        dx = self._head_x
        dy = self._head_y + breath_y

        w, h = frame.size
        nw = int(w * breath_scale)
        nh = int(h * breath_scale)
        if nw > 0 and nh > 0:
            frame = frame.resize((nw, nh), Image.LANCZOS)
            cx = (nw - w) // 2 - int(dx)
            cy = (nh - h) // 2 - int(dy)
            frame = frame.crop((cx, cy, cx + w, cy + h))

        return frame


# ═════════════════════════════════════════════════════════════════════
# Main GUI
# ═════════════════════════════════════════════════════════════════════
class OverwatchGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PMC Overwatch")
        self.geometry("860x920")
        self.minsize(640, 660)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=_BG)

        self._toggle_cb: Optional[Callable[[bool], None]] = None
        self._is_running = False
        self.shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []

        self._mode = "idle"
        self._phase = 0.0
        self._anim_id: Optional[str] = None
        self._pulse_id: Optional[str] = None
        self._pulse_vis = True
        self._dot_color = _MUTED

        # Alive engine
        self._engine = _AliveEngine(_CANVAS_W, _CANVAS_H)
        self._photo: Optional[ImageTk.PhotoImage] = None

        # Multi-stage blink
        self._blink_timer = 0.0
        self._blink_cd = random.uniform(2.5, 5.0)
        self._blink_stage = 0  # 0=open, 1=closing, 2=closed, 3=opening
        self._blink_dur = 0.0
        self._double_blink = False
        self._double_blink_count = 0

        # Speaking
        self._talk_timer = 0.0
        self._talk_pose = "talk_a"
        self._talk_cd = random.uniform(0.08, 0.18)

        # Voice bars
        self._bar_target = [0.0] * _N_BARS
        self._bar_current = [0.0] * _N_BARS

        self._build_header()
        self._build_agent()
        self._build_log()
        self._build_footer()
        self._start_anim()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        logger.info("Alive anime avatar GUI initialized")

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

        # Glow ring
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

            # Speaking ripples
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

        # Avatar
        frame = self._engine.render()
        if frame is not None:
            self._photo = ImageTk.PhotoImage(frame)
            cv.create_image(self._engine.avatar_x, self._engine.avatar_y,
                            image=self._photo, anchor="nw")

        # Voice bars
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

        # ── Multi-stage blink ─────────────────────────────────────────
        self._blink_timer += dt
        if self._blink_stage == 0:  # Open — waiting for next blink
            if self._blink_timer >= self._blink_cd:
                self._blink_stage = 1
                self._blink_dur = 0.0
                self._double_blink = random.random() < 0.15
                self._double_blink_count = 0
                self._engine.set_blink("half")

        elif self._blink_stage == 1:  # Closing (half → full)
            self._blink_dur += dt
            if self._blink_dur >= 0.04:
                self._blink_stage = 2
                self._blink_dur = 0.0
                self._engine.set_blink("close")

        elif self._blink_stage == 2:  # Fully closed
            self._blink_dur += dt
            if self._blink_dur >= 0.06:
                self._blink_stage = 3
                self._blink_dur = 0.0
                self._engine.set_blink("half")

        elif self._blink_stage == 3:  # Opening (full → half → open)
            self._blink_dur += dt
            if self._blink_dur >= 0.05:
                self._engine.set_blink("open")
                if self._double_blink and self._double_blink_count == 0:
                    # Double blink — go again after short pause
                    self._double_blink_count = 1
                    self._blink_stage = 0
                    self._blink_cd = random.uniform(0.15, 0.3)
                else:
                    self._blink_stage = 0
                    self._blink_timer = 0.0
                    self._blink_cd = random.uniform(2.5, 5.5)

        # ── Mode → compositing ────────────────────────────────────────
        if mode == "speaking":
            self._talk_timer += dt
            if self._talk_timer >= self._talk_cd:
                self._talk_timer = 0.0
                self._talk_cd = random.uniform(0.07, 0.16)
                r = random.random()
                if r < 0.35:
                    self._talk_pose = "talk_a"
                elif r < 0.7:
                    self._talk_pose = "talk_b"
                else:
                    self._talk_pose = "neutral"  # Brief lip closure
            self._engine.set_mode("speaking", self._talk_pose)

        elif mode == "thinking":
            self._engine.set_mode("thinking")

        elif mode == "listening":
            self._engine.set_mode("listening")

        else:
            self._engine.set_mode("idle")

    def _start_anim(self) -> None:
        self._tick()

    def _tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        dt = 1.0 / _FPS
        self._phase += dt

        self._update_state(dt)
        self._engine.update(dt)

        # Voice bars
        if self._mode == "speaking":
            for i in range(_N_BARS):
                cw = 1.0 - abs(i - _N_BARS // 2) / (_N_BARS // 2) * 0.4
                self._bar_target[i] = max(0.1, random.uniform(0.2, 1.0) * cw)
        elif self._mode == "thinking":
            for i in range(_N_BARS):
                w = (math.sin(self._phase * 3.0 + i * 0.3) + 1) * 0.5
                self._bar_target[i] = w * 0.14
        elif self._mode == "listening":
            for i in range(_N_BARS):
                self._bar_target[i] = 0.02 + (math.sin(self._phase * 0.5 + i * 0.15) + 1) * 0.02
        else:
            for i in range(_N_BARS):
                self._bar_target[i] = 0.0

        for i in range(_N_BARS):
            self._bar_current[i] += (self._bar_target[i] - self._bar_current[i]) * 0.3

        self._render_frame()
        self._anim_id = self.after(1000 // _FPS, self._tick)

    # ══ LOG ═══════════════════════════════════════════════════════════
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
            f, font=ctk.CTkFont(family=_mono, size=12), corner_radius=12,
            fg_color=_SURFACE, text_color=_TEXT, state="disabled",
            wrap="word", border_width=0)
        self._log.pack(fill="both", expand=True, padx=12, pady=(6, 12))

    # ══ FOOTER ════════════════════════════════════════════════════════
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
        ctk.CTkLabel(inner, text="v15.0", font=ctk.CTkFont(size=11),
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

    # ══ INTERNAL ═════════════════════════════════════════════════════
    def _do_log(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        labels = {
            "idle": ("OFFLINE", _MUTED), "listening": ("● LISTENING", _GREEN),
            "speaking": ("● SPEAKING", _CYAN), "thinking": ("● THINKING", _AMBER),
        }
        t, c = labels.get(mode, ("OFFLINE", _MUTED))
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
            self.after_cancel(self._pulse_id); self._pulse_id = None
        if pulse:
            self._pulse_vis = True; self._do_pulse()

    def _do_pulse(self) -> None:
        if self.shutdown_event.is_set(): return
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
            self._do_status("Starting..."); self.log("Overwatch activated")
        else:
            self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H)
            self._do_status("Offline"); self.log("Overwatch deactivated")
        if self._toggle_cb:
            self._toggle_cb(self._is_running)

    def _on_close(self) -> None:
        if self._anim_id: self.after_cancel(self._anim_id)
        if self._pulse_id: self.after_cancel(self._pulse_id)
        self.shutdown_event.set()
        for t in self._threads: t.join(timeout=3.0)
        self.destroy()


if __name__ == "__main__":
    app = OverwatchGUI()
    app.mainloop()
