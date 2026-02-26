"""
PMC Overwatch GUI — Premium anime avatar with live animated overlays.

Hybrid rendering approach:
  • High-quality anime girl sprite as base image
  • PIL-drawn animated mouth overlay (tracks audio amplitude)
  • PIL-drawn eyelid curtains (smooth multi-stage blinks)
  • Head sway, breathing bob, and scale via PIL transforms
  • Canvas glow rings and voice bars per mode

This gives the visual quality of hand-crafted art PLUS truly live
animation — the best of both worlds.
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
from PIL import Image, ImageDraw, ImageFilter, ImageTk

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


def _ease(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


# ═════════════════════════════════════════════════════════════════════
# Hybrid Anime Engine — sprite base + animated overlays
# ═════════════════════════════════════════════════════════════════════
class _AnimeEngine:
    """High quality anime face with live animated features.

    Loads the pre-rendered anime sprite as base, then draws animated
    overlays for mouth (amplitude-driven) and eyes (blink curtains)
    on each frame using PIL.
    """

    _SPRITES = {
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
        self._load_sprites()

        # Animation state
        self.amplitude = 0.0     # 0-1 mouth opening
        self.eyelid = 0.0        # 0=open, 1=closed
        self.smile = 0.0         # 0=neutral, 1=smile
        self.think_blend = 0.0   # 0=neutral, 1=thinking
        self.head_x = 0.0        # px offset
        self.head_y = 0.0        # px offset
        self.breath_scale = 1.0  # scale factor

        # Mouth region (fraction of image, tuned for the anime sprites)
        self._mouth_top = 0.60
        self._mouth_bot = 0.78
        self._mouth_cx = 0.50   # center x fraction

        # Eye region (fraction of image)
        self._eye_top = 0.28
        self._eye_bot = 0.46

    def _load_sprites(self) -> None:
        for name, fn in self._SPRITES.items():
            path = os.path.join(_ASSET_DIR, fn)
            if os.path.exists(path):
                try:
                    img = Image.open(path).convert("RGBA")
                    img = img.resize((self._size, self._size), Image.LANCZOS)
                    self._sprites[name] = img
                except Exception:
                    logger.warning("Failed to load sprite: %s", path)

        # Fallbacks
        if "neutral" not in self._sprites:
            if self._sprites:
                self._sprites["neutral"] = next(iter(self._sprites.values()))
            else:
                # Create placeholder
                self._sprites["neutral"] = Image.new("RGBA", (self._size, self._size), (20, 20, 30, 255))

        for fallback in ("smile", "think", "blink", "talk_a", "talk_b"):
            if fallback not in self._sprites:
                self._sprites[fallback] = self._sprites["neutral"]

    def render(self) -> Image.Image:
        """Render one frame with animated overlays."""
        s = self._size

        # ── Select base sprite based on state ─────────────────────────
        if self.think_blend > 0.5:
            base = self._sprites["think"].copy()
        elif self.smile > 0.5:
            base = self._sprites["smile"].copy()
        else:
            base = self._sprites["neutral"].copy()

        # ── Mouth: blend talk sprites based on amplitude ──────────────
        if self.amplitude > 0.08:
            if self.amplitude < 0.35:
                talk = self._sprites["talk_a"]
                blend_t = min(1.0, self.amplitude / 0.35)
            else:
                talk = self._sprites["talk_b"]
                blend_t = min(1.0, (self.amplitude - 0.35) / 0.5 + 0.5)

            # Mouth region mask — fade in the talk sprite's mouth
            mask = Image.new("L", (s, s), 0)
            draw = ImageDraw.Draw(mask)
            mt = int(self._mouth_top * s)
            mb = int(self._mouth_bot * s)
            mcx = int(self._mouth_cx * s)
            mw = int(s * 0.32)
            # Elliptical mask for mouth region
            draw.ellipse(
                [mcx - mw, mt, mcx + mw, mb],
                fill=int(255 * _ease(blend_t))
            )
            # Soften edges
            mask = mask.filter(ImageFilter.GaussianBlur(radius=6))
            base.paste(talk, mask=mask)

        # ── Eyes: eyelid curtain for blinks ───────────────────────────
        if self.eyelid > 0.05:
            # Sample skin color from the forehead area for natural eyelid
            try:
                skin_sample = base.getpixel((s // 2, int(s * 0.22)))
                skin_color = skin_sample[:3] if len(skin_sample) >= 3 else (232, 196, 160)
            except Exception:
                skin_color = (232, 196, 160)

            overlay = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            ov_draw = ImageDraw.Draw(overlay)

            et = int(self._eye_top * s)
            eb = int(self._eye_bot * s)
            eye_h = eb - et

            # Eyelid drops from top of eye region
            lid_drop = int(self.eyelid * eye_h * 1.1)
            if lid_drop > 2:
                lid_alpha = min(255, int(self.eyelid * 280))
                r, g, b = skin_color

                # Left eye region
                lex = int(s * 0.28)
                rew = int(s * 0.18)
                ov_draw.rounded_rectangle(
                    [lex - rew, et - 4, lex + rew, et + lid_drop],
                    radius=8,
                    fill=(r, g, b, lid_alpha)
                )

                # Right eye region
                rex = int(s * 0.72)
                ov_draw.rounded_rectangle(
                    [rex - rew, et - 4, rex + rew, et + lid_drop],
                    radius=8,
                    fill=(r, g, b, lid_alpha)
                )

                # Soften for natural look
                overlay = overlay.filter(ImageFilter.GaussianBlur(radius=3))
                base = Image.alpha_composite(base, overlay)

        # ── Head motion + breathing ───────────────────────────────────
        w, h = base.size
        nw = int(w * self.breath_scale)
        nh = int(h * self.breath_scale)
        if nw > 0 and nh > 0 and (nw != w or nh != h):
            base = base.resize((nw, nh), Image.LANCZOS)
            cx = (nw - w) // 2 - int(self.head_x)
            cy = (nh - h) // 2 - int(self.head_y)
            base = base.crop((cx, cy, cx + w, cy + h))
        elif abs(self.head_x) > 0.5 or abs(self.head_y) > 0.5:
            # Apply head offset via crop
            shifted = Image.new("RGBA", (w, h), (10, 14, 20, 255))
            ox = int(self.head_x)
            oy = int(self.head_y)
            shifted.paste(base, (ox, oy))
            base = shifted

        return base


# ═════════════════════════════════════════════════════════════════════
# Main GUI
# ═════════════════════════════════════════════════════════════════════
class OverwatchGUI(ctk.CTk):
    """PMC Overwatch — premium anime avatar with live animation."""

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

        # Compute avatar size
        avatar_size = min(_CANVAS_W - 40, _CANVAS_H - 60)
        self._engine = _AnimeEngine(avatar_size)
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

        # Speaking — amplitude driven
        self._amplitude = 0.0
        self._amplitude_target = 0.0

        # Emotion
        self._emotion = "neutral"
        self._emotion_timer = 0.0

        # Input mode display
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
        logger.info("Premium anime avatar GUI initialized")

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

        # Avatar rendering
        frame = self._engine.render()
        if frame is not None:
            self._photo = ImageTk.PhotoImage(frame)
            cv.create_image(self._avatar_x, self._avatar_y,
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
        if self._blink_stage == 0:
            if self._blink_timer >= self._blink_cd:
                self._blink_stage = 1
                self._blink_dur = 0.0
                self._double_blink = random.random() < 0.15
                self._double_blink_count = 0
        elif self._blink_stage == 1:  # Half close
            self._blink_dur += dt
            self._engine.eyelid = _lerp(self._engine.eyelid, 0.55, 0.45)
            if self._blink_dur >= 0.05:
                self._blink_stage = 2
                self._blink_dur = 0.0
        elif self._blink_stage == 2:  # Full close
            self._blink_dur += dt
            self._engine.eyelid = _lerp(self._engine.eyelid, 1.0, 0.55)
            if self._blink_dur >= 0.07:
                self._blink_stage = 3
                self._blink_dur = 0.0
        elif self._blink_stage == 3:  # Opening
            self._blink_dur += dt
            self._engine.eyelid = _lerp(self._engine.eyelid, 0.0, 0.35)
            if self._blink_dur >= 0.06:
                self._engine.eyelid = 0.0
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

        self._engine.head_x = self._head_x
        self._engine.head_y = self._head_y + breath_y
        self._engine.breath_scale = breath_scale

        # ── Mode-specific ────────────────────────────────────────────
        if mode == "speaking":
            self._amplitude += (self._amplitude_target - self._amplitude) * 0.4
            self._engine.amplitude = self._amplitude
            self._engine.smile = _lerp(self._engine.smile, 0.15, 0.04)
            self._engine.think_blend = _lerp(self._engine.think_blend, 0.0, 0.1)

        elif mode == "thinking":
            self._engine.amplitude = _lerp(self._engine.amplitude, 0.0, 0.1)
            self._engine.think_blend = _lerp(self._engine.think_blend, 1.0, 0.06)
            self._engine.smile = _lerp(self._engine.smile, 0.0, 0.05)

        elif mode == "listening":
            self._engine.amplitude = _lerp(self._engine.amplitude, 0.0, 0.1)
            self._engine.smile = _lerp(self._engine.smile, 0.2, 0.03)
            self._engine.think_blend = _lerp(self._engine.think_blend, 0.0, 0.1)

        else:  # idle
            self._engine.amplitude = _lerp(self._engine.amplitude, 0.0, 0.08)
            self._engine.smile = _lerp(self._engine.smile, 0.05, 0.02)
            self._engine.think_blend = _lerp(self._engine.think_blend, 0.0, 0.05)

        # ── Emotion ──────────────────────────────────────────────────
        if self._emotion == "happy":
            self._engine.smile = _lerp(self._engine.smile, 0.9, 0.06)
            self._emotion_timer += dt
            if self._emotion_timer > 3.0:
                self._emotion = "neutral"
                self._emotion_timer = 0.0

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
        ctk.CTkLabel(inner, text="v0.18.0", font=ctk.CTkFont(size=11),
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
        """Set current audio amplitude for lip sync (0.0-1.0)."""
        self._amplitude_target = amp
    def set_emotion(self, emotion: str) -> None:
        """Set avatar emotion: 'neutral', 'happy', 'curious'."""
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
    print("PREMIUM ANIME DEMO PASSED")
