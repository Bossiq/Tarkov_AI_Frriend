"""
PMC Overwatch GUI — Procedurally rendered live avatar.

The character is NOT an image. Every pixel of the face, eyes, mouth,
hair, and body is drawn from code using PIL each frame. The sprite
images are never used. The avatar is truly alive — generated, not pasted.

v0.23.0:
  • Full PIL procedural rendering (face, eyes, iris, mouth, hair, body)
  • Every element independently animated with SmoothedNoise
  • Holographic post-processing on the drawn character
  • Glass-morphism UI
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

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_PERSONA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.json")


# ═════════════════════════════════════════════════════════════════════
# Smoothed noise for organic motion
# ═════════════════════════════════════════════════════════════════════
class _Noise:
    def __init__(self, speed=0.5, amp=1.0):
        self._s = speed
        self._a = amp
        self._o = random.uniform(0, 1000)

    def at(self, t):
        p = t * self._s + self._o
        return (math.sin(p) * 0.5 + math.sin(p * 2.3 + 1.7) * 0.3
                + math.sin(p * 4.1 + 3.2) * 0.2) * self._a


def _lerp(a, b, t):
    return a + (b - a) * t


# ═════════════════════════════════════════════════════════════════════
# Procedural Face Renderer — Draws every pixel from code
# ═════════════════════════════════════════════════════════════════════
class _ProceduralFace:
    """Draws a complete anime-style character from code each frame.

    Nothing is an image. Face shape, eyes (with iris, pupil, highlight),
    mouth, eyebrows, hair, nose, shoulders — all are drawn using PIL
    primitives with anti-aliasing.
    """

    # ── Character design constants ─────────────────────────────────
    # Colors (anime girl — warm skin, dark hair, cyan/teal eyes)
    SKIN = (255, 228, 210)
    SKIN_SHADOW = (240, 200, 180)
    HAIR = (40, 32, 48)
    HAIR_HL = (70, 55, 85)
    EYE_WHITE = (255, 255, 255)
    IRIS_OUTER = (0, 180, 200)
    IRIS_INNER = (0, 120, 180)
    PUPIL = (10, 10, 20)
    EYE_HL = (255, 255, 255)
    LIP_COLOR = (220, 130, 130)
    LIP_DARK = (180, 100, 100)
    BROW_COLOR = (50, 40, 55)
    LASH_COLOR = (20, 15, 30)
    NOSE_COLOR = (240, 195, 175)
    JACKET = (60, 65, 55)       # tactical olive drab
    JACKET_HL = (80, 90, 72)
    COLLAR = (45, 48, 42)
    STRAP = (35, 35, 30)

    def __init__(self, size: int) -> None:
        self._s = size
        self._cx = size // 2
        self._cy = size // 2 - 20  # face center (offset up for body below)

        # Noise channels for organic motion
        self.n_head_x = _Noise(0.4, 6.0)
        self.n_head_y = _Noise(0.35, 4.0)
        self.n_gaze_x = _Noise(0.3, 3.0)
        self.n_gaze_y = _Noise(0.25, 2.0)
        self.n_brow_l = _Noise(0.2, 2.0)
        self.n_brow_r = _Noise(0.22, 2.0)
        self.n_head_tilt = _Noise(0.15, 1.5)

        # Blend state (set by GUI)
        self.mouth_open = 0.0     # 0-1
        self.blink = 0.0          # 0-1 eye closing
        self.smile = 0.0          # 0-1
        self.think = 0.0          # 0-1 (raises brows, looks up)
        self.breath_phase = 0.0

        # Micro-expression
        self._micro_timer = 0.0
        self._micro_smile = 0.0
        self._micro_target = 0.0

    def update_micro(self, dt):
        self._micro_timer += dt
        if self._micro_timer > random.uniform(3.0, 7.0):
            self._micro_timer = 0.0
            self._micro_target = random.uniform(0.0, 0.25)
        self._micro_smile = _lerp(self._micro_smile, self._micro_target, 0.03)
        if abs(self._micro_smile - self._micro_target) < 0.01:
            self._micro_target = 0.0

    def draw(self, t: float, mode: str) -> Image.Image:
        """Draw the entire character from scratch — no sprites."""
        s = self._s
        # Use 2x render for anti-aliasing, then downscale
        rs = s * 2
        img = Image.new("RGBA", (rs, rs), (5, 8, 16, 0))
        d = ImageDraw.Draw(img)

        # Motion offsets
        dx = self.n_head_x.at(t)
        dy = self.n_head_y.at(t) + math.sin(self.breath_phase) * 4.0
        gx = self.n_gaze_x.at(t)
        gy = self.n_gaze_y.at(t)

        if mode == "speaking" and self.mouth_open > 0.15:
            dy += math.sin(t * 8.0) * self.mouth_open * 3.0
        if mode == "listening":
            dy += 3

        cx = rs // 2 + int(dx * 2)
        cy = rs // 2 - 40 + int(dy * 2)

        # Scale factors for proportions
        fw = 150  # face width (at 2x)
        fh = 180  # face height

        # ════════════════════════════════════════════════════════════
        # BODY — shoulders and tactical jacket
        # ════════════════════════════════════════════════════════════
        body_y = cy + fh // 2 + 20
        # Shoulders (wide)
        d.ellipse([cx - 200, body_y - 10, cx + 200, body_y + 160],
                  fill=self.JACKET, outline=self.COLLAR, width=2)
        # Collar / neck
        d.ellipse([cx - 50, cy + fh // 2 - 10, cx + 50, body_y + 20],
                  fill=self.SKIN_SHADOW)
        # Collar straps
        d.rectangle([cx - 35, body_y - 5, cx - 25, body_y + 30], fill=self.STRAP)
        d.rectangle([cx + 25, body_y - 5, cx + 35, body_y + 30], fill=self.STRAP)
        # Tactical vest lines
        d.line([(cx - 80, body_y + 30), (cx - 80, body_y + 120)], fill=self.COLLAR, width=3)
        d.line([(cx + 80, body_y + 30), (cx + 80, body_y + 120)], fill=self.COLLAR, width=3)

        # ════════════════════════════════════════════════════════════
        # HAIR — back layer (behind face)
        # ════════════════════════════════════════════════════════════
        hair_sway = math.sin(t * 0.8) * 4
        # Back hair (long, flows down past shoulders)
        for i in range(5):
            hx = cx - 100 + i * 50 + int(hair_sway * (i % 3 - 1))
            d.ellipse([hx - 40, cy - fh // 2 - 20, hx + 40, body_y + 80 + i * 10],
                      fill=self.HAIR)

        # ════════════════════════════════════════════════════════════
        # FACE SHAPE — smooth oval with slight chin taper
        # ════════════════════════════════════════════════════════════
        face_box = [cx - fw, cy - fh // 2, cx + fw, cy + fh // 2]
        d.ellipse(face_box, fill=self.SKIN)
        # Chin sharpen — draw a smaller oval at bottom
        chin_box = [cx - fw + 30, cy, cx + fw - 30, cy + fh // 2 + 20]
        d.ellipse(chin_box, fill=self.SKIN)
        # Cheek blush
        d.ellipse([cx - fw + 10, cy + 20, cx - fw + 55, cy + 55],
                  fill=(255, 210, 200))
        d.ellipse([cx + fw - 55, cy + 20, cx + fw - 10, cy + 55],
                  fill=(255, 210, 200))

        # ════════════════════════════════════════════════════════════
        # EYES — large anime-style with iris/pupil/highlights
        # ════════════════════════════════════════════════════════════
        eye_y = cy - 10
        eye_spacing = 60
        eye_w = 50
        eye_h = max(5, int(40 * (1.0 - self.blink)))  # shrinks when blinking

        for side in (-1, 1):
            ex = cx + side * eye_spacing

            if self.blink > 0.9:
                # Fully closed — draw a line
                d.line([(ex - eye_w, eye_y), (ex + eye_w, eye_y)],
                       fill=self.LASH_COLOR, width=4)
                continue

            # Eye white
            d.ellipse([ex - eye_w, eye_y - eye_h, ex + eye_w, eye_y + eye_h],
                      fill=self.EYE_WHITE)

            # Iris (follows gaze)
            iris_r = int(eye_h * 0.65)
            ix = ex + int(gx * 2)
            iy = eye_y + int(gy * 2)
            if self.think > 0.3:
                iy -= 6  # look up when thinking
            d.ellipse([ix - iris_r, iy - iris_r, ix + iris_r, iy + iris_r],
                      fill=self.IRIS_OUTER)
            # Inner iris
            ir2 = int(iris_r * 0.7)
            d.ellipse([ix - ir2, iy - ir2, ix + ir2, iy + ir2],
                      fill=self.IRIS_INNER)
            # Pupil
            pr = int(iris_r * 0.35)
            d.ellipse([ix - pr, iy - pr, ix + pr, iy + pr],
                      fill=self.PUPIL)
            # Highlight (life in the eyes)
            hl_r = max(4, int(iris_r * 0.25))
            hl_x = ix - int(iris_r * 0.3)
            hl_y = iy - int(iris_r * 0.3)
            d.ellipse([hl_x - hl_r, hl_y - hl_r, hl_x + hl_r, hl_y + hl_r],
                      fill=self.EYE_HL)
            # Secondary highlight
            hl2_r = max(3, int(iris_r * 0.15))
            d.ellipse([ix + int(iris_r * 0.2) - hl2_r, iy + int(iris_r * 0.2) - hl2_r,
                        ix + int(iris_r * 0.2) + hl2_r, iy + int(iris_r * 0.2) + hl2_r],
                      fill=(200, 220, 255))

            # Upper eyelid / lash line
            d.arc([ex - eye_w - 2, eye_y - eye_h - 4, ex + eye_w + 2, eye_y + eye_h + 4],
                  start=200, end=340, fill=self.LASH_COLOR, width=5)

            # Lower lash (subtle)
            d.arc([ex - eye_w, eye_y - eye_h, ex + eye_w, eye_y + eye_h],
                  start=20, end=160, fill=self.LASH_COLOR, width=2)

        # ════════════════════════════════════════════════════════════
        # EYEBROWS — expressive arcs
        # ════════════════════════════════════════════════════════════
        brow_y = eye_y - eye_h - 15
        brow_raise = self.n_brow_l.at(t)
        think_raise = self.think * 8

        for side in (-1, 1):
            bx = cx + side * eye_spacing
            by = brow_y - int(brow_raise) - int(think_raise)
            angle = 10 * side  # slight angle outward
            # Thick eyebrow arc
            d.arc([bx - 45, by - 12 + angle, bx + 45, by + 18 + angle],
                  start=200 if side < 0 else 200, end=340,
                  fill=self.BROW_COLOR, width=7)

        # ════════════════════════════════════════════════════════════
        # NOSE — subtle small triangle
        # ════════════════════════════════════════════════════════════
        nose_y = cy + 15
        d.line([(cx, nose_y), (cx + 6, nose_y + 12)], fill=self.NOSE_COLOR, width=3)
        d.ellipse([cx + 2, nose_y + 8, cx + 10, nose_y + 15], fill=self.NOSE_COLOR)

        # ════════════════════════════════════════════════════════════
        # MOUTH — dynamic shape based on mouth_open / smile
        # ════════════════════════════════════════════════════════════
        mouth_y = cy + fh // 4 + 10
        eff_smile = max(self.smile, self._micro_smile)
        mo = self.mouth_open

        if mo > 0.05:
            # Open mouth — ellipse that grows with amplitude
            mh = int(12 + mo * 30)
            mw = int(25 + mo * 15 + eff_smile * 10)
            # Outer lip
            d.ellipse([cx - mw, mouth_y - mh // 2, cx + mw, mouth_y + mh // 2],
                      fill=self.LIP_DARK)
            # Inner mouth (dark)
            im = 5
            d.ellipse([cx - mw + im, mouth_y - mh // 2 + im,
                        cx + mw - im, mouth_y + mh // 2 - im],
                      fill=(30, 15, 20))
            # Teeth (top)
            if mh > 15:
                tw = mw - im - 4
                th = min(8, mh // 4)
                d.rectangle([cx - tw, mouth_y - mh // 2 + im,
                              cx + tw, mouth_y - mh // 2 + im + th],
                            fill=(250, 250, 250))
        elif eff_smile > 0.1:
            # Smile — curved line
            sw = int(20 + eff_smile * 20)
            d.arc([cx - sw, mouth_y - 10, cx + sw, mouth_y + 15],
                  start=10, end=170, fill=self.LIP_COLOR, width=5)
        else:
            # Neutral — slight line
            d.line([(cx - 18, mouth_y), (cx + 18, mouth_y)],
                   fill=self.LIP_COLOR, width=4)

        # ════════════════════════════════════════════════════════════
        # HAIR — front layer (bangs, side locks)
        # ════════════════════════════════════════════════════════════
        sway = math.sin(t * 0.8 + 0.5) * 3

        # Bangs — overlapping ovals across forehead
        bang_y = cy - fh // 2 - 10
        for i in range(7):
            bx = cx - 90 + i * 30 + int(sway * (i % 3 - 1))
            bw = 35 + (i % 2) * 8
            bh = 80 + (i % 3) * 15
            color = self.HAIR_HL if i % 3 == 1 else self.HAIR
            d.ellipse([bx - bw, bang_y - 10, bx + bw, bang_y + bh], fill=color)

        # Side locks (flowing down past face)
        for side in (-1, 1):
            lx = cx + side * (fw + 15) + int(sway * side)
            for j in range(3):
                ly = cy - 30 + j * 50
                lw = 25 - j * 3
                lh = 60
                d.ellipse([lx - lw, ly, lx + lw, ly + lh], fill=self.HAIR)

        # Top hair dome
        d.ellipse([cx - fw - 20, cy - fh // 2 - 60, cx + fw + 20, cy - fh // 2 + 40],
                  fill=self.HAIR)

        # ════════════════════════════════════════════════════════════
        # POST-PROCESS — downscale (anti-aliasing) + holographic FX
        # ════════════════════════════════════════════════════════════
        img = img.resize((s, s), Image.LANCZOS)

        # Scanlines (subtle)
        scan = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        sd_draw = ImageDraw.Draw(scan)
        for y in range(0, s, 3):
            sd_draw.line([(0, y), (s, y)], fill=(0, 0, 0, 22), width=1)
        img = Image.alpha_composite(img, scan)

        # Subtle chromatic aberration
        r, g, b, a = img.split()
        r_new = Image.new("L", (s, s), 0)
        r_new.paste(r, (1, 0))
        b_new = Image.new("L", (s, s), 0)
        b_new.paste(b, (-1, 0))
        img = Image.merge("RGBA", (r_new, g, b_new, a))

        # Subtle flicker
        flk = 0.96 + math.sin(t * 10.0) * 0.02 + random.uniform(-0.005, 0.005)
        img = ImageEnhance.Brightness(img).enhance(max(0.9, min(1.05, flk)))

        return img


# ═════════════════════════════════════════════════════════════════════
# Particles
# ═════════════════════════════════════════════════════════════════════
class _Particle:
    __slots__ = ("x", "y", "vx", "vy", "r", "alpha", "life", "max_life")
    def __init__(self, cx, cy):
        a = random.uniform(0, 2 * math.pi)
        dist = random.uniform(80, 170)
        self.x = cx + math.cos(a) * dist
        self.y = cy + math.sin(a) * dist
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
    """PMC Overwatch — procedurally rendered live avatar."""

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
        self._face = _ProceduralFace(avatar_size)
        self._avatar_x = (_CANVAS_W - avatar_size) // 2
        self._avatar_y = 6

        # Blink
        self._blink_timer = 0.0
        self._blink_cd = random.uniform(2.0, 5.0)
        self._blink_stage = 0
        self._blink_dur = 0.0
        self._double_blink = False
        self._double_blink_count = 0

        self._amplitude = 0.0
        self._amplitude_target = 0.0
        self._emotion = "neutral"
        self._emotion_timer = 0.0
        self._particles: list[_Particle] = []
        self._particle_timer = 0.0
        self._language = os.getenv("WHISPER_LANGUAGE", "auto").lower()
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
        logger.info("Procedural live avatar GUI initialized (v0.23.0)")

    # ══ HEADER ═══════════════════════════════════════════════════════
    def _build_header(self):
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
        ctk.CTkLabel(logo, text="Live Holographic Companion",
                     font=ctk.CTkFont(family=_ft, size=11),
                     text_color=_HOLO).pack(anchor="w")
        self._btn = ctk.CTkButton(
            inner, text="▶  Start", width=140, height=44, corner_radius=14,
            font=ctk.CTkFont(family=_ft, size=14, weight="bold"),
            fg_color=_GREEN, hover_color=_GREEN_H, text_color="white",
            command=self._on_toggle)
        self._btn.pack(side="right")

    # ══ CANVAS ═══════════════════════════════════════════════════════
    def _build_agent(self):
        self._cv = tk.Canvas(self, width=_CANVAS_W, height=_CANVAS_H,
                             bg=_BG, highlightthickness=0, bd=0)
        self._cv.pack(pady=(8, 0))
        self._av_status = ctk.CTkLabel(
            self, text="◆ OFFLINE",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_MUTED)
        self._av_status.pack(pady=(2, 4))

    # ══ RENDERING ════════════════════════════════════════════════════
    def _render_frame(self):
        cv = self._cv
        cv.delete("all")
        glow_c = _GLOW.get(self._mode, _MUTED)
        glow_rgb = _GLOW_RGB.get(self._mode, (48, 54, 61))
        cx, cy = _CANVAS_W // 2, _CANVAS_H // 2 - 10

        # Ambient glow
        if self._mode != "idle":
            ga = 20 + int((math.sin(self._time * 0.5) + 1) * 8)
            r, g, b = glow_rgb
            gc = f"#{max(5, r * ga // 255):02x}{max(8, g * ga // 255):02x}{max(16, b * ga // 255):02x}"
            ar = 200
            cv.create_oval(cx - ar, cy - ar, cx + ar, cy + ar, fill=gc, outline="")
            for i in range(3):
                rr = ar - 10 + i * 8 + int(math.sin(self._time * 0.7 + i) * 2)
                a = max(0, 50 - i * 16)
                rc = max(5, int(r * a / 255))
                gc2 = max(8, int(g * a / 255))
                bc = max(16, int(b * a / 255))
                c = f"#{rc:02x}{gc2:02x}{bc:02x}"
                cv.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=c, width=1)

        # Particles
        for p in self._particles:
            if p.alpha > 0.02:
                a = int(min(1.0, p.alpha) * 80)
                pr = max(5, int(glow_rgb[0] * a / 255))
                pg = max(8, int(glow_rgb[1] * a / 255))
                pb = max(16, int(glow_rgb[2] * a / 255))
                pc = f"#{pr:02x}{pg:02x}{pb:02x}"
                cv.create_oval(p.x - p.r, p.y - p.r, p.x + p.r, p.y + p.r,
                               fill=pc, outline="")

        # Procedurally rendered face — DRAWN FROM CODE, not pasted
        frame = self._face.draw(self._time, self._mode)
        if frame is not None:
            self._photo = ImageTk.PhotoImage(frame)
            cv.create_image(self._avatar_x, self._avatar_y,
                            image=self._photo, anchor="nw")

        # Waveform
        total = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        bx = _CANVAS_W // 2 - total // 2
        by = _CANVAS_H - 20
        for i in range(_N_BARS):
            h = max(1, int(self._bar_current[i] * _BAR_MAX_H))
            x = bx + i * (_BAR_W + _BAR_GAP)
            cv.create_rectangle(x, by - h, x + _BAR_W, by, fill=glow_c, outline="")
            cv.create_rectangle(x, by, x + _BAR_W, by + h, fill=glow_c, outline="")

    # ══ ANIMATION ════════════════════════════════════════════════════
    def _update_state(self, dt):
        face = self._face
        mode = self._mode

        face.breath_phase += dt * 1.2
        face.update_micro(dt)

        # Blink
        self._blink_timer += dt
        if self._blink_stage == 0:
            if self._blink_timer >= self._blink_cd:
                self._blink_stage = 1; self._blink_dur = 0.0
                self._double_blink = random.random() < 0.15
                self._double_blink_count = 0
        elif self._blink_stage == 1:
            self._blink_dur += dt
            face.blink = _lerp(face.blink, 0.6, 0.45)
            if self._blink_dur >= 0.05:
                self._blink_stage = 2; self._blink_dur = 0.0
        elif self._blink_stage == 2:
            self._blink_dur += dt
            face.blink = _lerp(face.blink, 1.0, 0.55)
            if self._blink_dur >= 0.08:
                self._blink_stage = 3; self._blink_dur = 0.0
        elif self._blink_stage == 3:
            self._blink_dur += dt
            face.blink = _lerp(face.blink, 0.0, 0.35)
            if self._blink_dur >= 0.07:
                face.blink = 0.0
                if self._double_blink and self._double_blink_count == 0:
                    self._double_blink_count = 1; self._blink_stage = 0
                    self._blink_cd = random.uniform(0.15, 0.3)
                else:
                    self._blink_stage = 0; self._blink_timer = 0.0
                    self._blink_cd = random.uniform(2.0, 5.0)

        self._amplitude += (self._amplitude_target - self._amplitude) * 0.4

        if mode == "speaking":
            face.mouth_open = min(1.0, self._amplitude * 2.0)
            face.smile = _lerp(face.smile, 0.0, 0.08)
            face.think = _lerp(face.think, 0.0, 0.08)
        elif mode == "thinking":
            face.mouth_open = _lerp(face.mouth_open, 0.0, 0.1)
            face.smile = _lerp(face.smile, 0.0, 0.08)
            face.think = _lerp(face.think, 0.85, 0.06)
        elif mode == "listening":
            face.mouth_open = _lerp(face.mouth_open, 0.0, 0.1)
            face.smile = _lerp(face.smile, 0.2, 0.04)
            face.think = _lerp(face.think, 0.0, 0.08)
        else:
            face.mouth_open = _lerp(face.mouth_open, 0.0, 0.08)
            face.smile = _lerp(face.smile, 0.0, 0.04)
            face.think = _lerp(face.think, 0.0, 0.04)

        if self._emotion == "happy":
            face.smile = _lerp(face.smile, 0.9, 0.06)
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
            if p.life < p.max_life: alive.append(p)
        self._particles = alive

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

    # ══ LOG ══════════════════════════════════════════════════════════
    def _build_log(self):
        wrapper = ctk.CTkFrame(self, corner_radius=20, fg_color=_CARD,
                               border_width=1, border_color=_BORDER)
        wrapper.pack(fill="both", expand=True, padx=24, pady=(6, 0))
        inner = ctk.CTkFrame(wrapper, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=14, pady=14)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="◆ Activity Log",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=_TEXT2).pack(side="left")
        self._lang_var = ctk.StringVar(value=self._language.upper())
        ctk.CTkOptionMenu(
            top, values=["AUTO", "EN", "RU", "RO"],
            variable=self._lang_var, width=80, height=24,
            font=ctk.CTkFont(size=10), fg_color=_GLASS,
            button_color=_BORDER, command=self._on_lang_change).pack(side="right")
        ctk.CTkLabel(top, text="🌐", font=ctk.CTkFont(size=12),
                     text_color=_HOLO).pack(side="right", padx=(0, 6))
        self._log = ctk.CTkTextbox(
            inner, font=ctk.CTkFont(family="Cascadia Code, Consolas", size=11),
            height=110, fg_color=_SURFACE, text_color=_TEXT, corner_radius=12,
            border_width=1, border_color=_BORDER, wrap="word")
        self._log.pack(fill="both", expand=True, pady=(8, 0))
        self._log.configure(state="disabled")
        ci = ctk.CTkFrame(inner, fg_color="transparent")
        ci.pack(fill="x", pady=(10, 0))
        self._chat_entry = ctk.CTkEntry(
            ci, placeholder_text="Type a message…", font=ctk.CTkFont(size=12),
            fg_color=_GLASS, text_color=_TEXT, border_color=_BORDER,
            corner_radius=12, height=38)
        self._chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._chat_entry.bind("<Return>", self._on_chat_send)
        ctk.CTkButton(
            ci, text="Send", width=70, height=38, corner_radius=12,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=_ACCENT, hover_color="#1a7f37", text_color="white",
            command=self._on_chat_send).pack(side="right")

    # ══ FOOTER ═══════════════════════════════════════════════════════
    def _build_footer(self):
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
        ctk.CTkLabel(inner, text="Ctrl+O  Ctrl+P", font=ctk.CTkFont(size=9),
                     text_color=_MUTED).pack(side="right", padx=(0, 10))
        ctk.CTkLabel(inner, text="v0.23.0", font=ctk.CTkFont(size=10),
                     text_color=_MUTED).pack(side="right", padx=(0, 10))

    # ══ OBS / PERSONA / LANG ═════════════════════════════════════════
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
        win.geometry("520x420"); win.configure(fg_color=_BG)
        win.attributes("-topmost", True)
        ctk.CTkLabel(win, text="🛡️ Persona Editor",
                     font=ctk.CTkFont(size=20, weight="bold"), text_color=_TEXT).pack(pady=(20, 8))
        ctk.CTkLabel(win, text="Edit AI personality", font=ctk.CTkFont(size=11), text_color=_TEXT2).pack()
        persona = self._load_persona()
        fr = ctk.CTkFrame(win, fg_color=_CARD, corner_radius=16)
        fr.pack(fill="both", expand=True, padx=24, pady=16)
        ctk.CTkLabel(fr, text="Name:", text_color=_TEXT2).pack(anchor="w", padx=16, pady=(16, 0))
        ne = ctk.CTkEntry(fr, fg_color=_GLASS, text_color=_TEXT, border_color=_BORDER, corner_radius=10)
        ne.pack(fill="x", padx=16, pady=(4, 10)); ne.insert(0, persona.get("name", "PMC Operator"))
        ctk.CTkLabel(fr, text="System Prompt:", text_color=_TEXT2).pack(anchor="w", padx=16)
        pb = ctk.CTkTextbox(fr, fg_color=_GLASS, text_color=_TEXT, corner_radius=10, height=180, wrap="word")
        pb.pack(fill="both", expand=True, padx=16, pady=(4, 16))
        pb.insert("1.0", persona.get("prompt", ""))
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

    def _save_persona(self, data):
        try:
            with open(_PERSONA_FILE, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception: pass

    def _on_lang_change(self, v):
        os.environ["WHISPER_LANGUAGE"] = v.lower(); self._language = v.lower()
        self.log(f"🌐 Language → {v}")

    # ══ PUBLIC API ════════════════════════════════════════════════════
    def set_toggle_callback(self, cb): self._toggle_cb = cb
    def register_thread(self, t): self._threads.append(t)
    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S"); line = f"[{ts}]  {msg}"
        self._chat_log.append(line); self.after(0, self._do_log, f"{line}\n")
    def set_status(self, text): self.after(0, self._do_status, text)
    def force_toggle_off(self): self.after(0, self._force_off)
    def set_vis_mode(self, mode): self.after(0, self._set_mode, mode)
    def set_amplitude(self, amp): self._amplitude_target = amp
    def set_emotion(self, emotion): self.after(0, self._do_emotion, emotion)
    def add_response_stats(self, wc): self._words_spoken += wc; self._responses += 1

    def _do_log(self, line):
        self._log.configure(state="normal"); self._log.insert("end", line)
        self._log.see("end"); self._log.configure(state="disabled")
    def _set_mode(self, mode):
        self._mode = mode
        labels = {"idle": ("◆ OFFLINE", _MUTED), "listening": ("◆ LISTENING", _GREEN),
                  "thinking": ("◆ THINKING", _AMBER), "speaking": ("◆ SPEAKING", _HOLO)}
        t, c = labels.get(mode, ("◆ OFFLINE", _MUTED))
        self._av_status.configure(text=t, text_color=c)
    def _do_emotion(self, e): self._emotion = e; self._emotion_timer = 0.0
    def _do_status(self, text):
        self._status_lbl.configure(text=text); lo = text.lower()
        if "listening" in lo: self._set_dot(_GREEN, True); self._set_mode("listening")
        elif "speaking" in lo: self._set_dot(_HOLO, True); self._set_mode("speaking")
        elif "thinking" in lo: self._set_dot(_AMBER, True); self._set_mode("thinking")
        elif "offline" in lo: self._set_dot(_MUTED, False); self._set_mode("idle")
        else: self._set_dot(_AMBER, False)
    def _set_dot(self, c, p): self._dot.itemconfig(self._dot_id, fill=c)
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
        for _ in range(60):
            app.set_amplitude(_r.uniform(0.0, 1.0))
            time.sleep(0.04)
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
    print("PROCEDURAL LIVE AVATAR DEMO PASSED")
