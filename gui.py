"""
PMC Overwatch GUI — Live vector-rendered face on Tkinter Canvas.

All facial features (head, eyes, eyebrows, mouth, nose) are drawn
as geometric shapes directly on the Canvas. No images are loaded.

Animation features:
  • Smooth bezier mouth morphing driven by audio amplitude
  • Multi-stage eyelid blinks (half-close → full → half-open → open)
  • Eyebrow expressions (neutral, raised, furrowed)
  • Head sway, tilt, and breathing bob
  • Gaze wander (iris/pupil offset)
  • Glow ring and voice bars per mode
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

# ── Face colors (warm PMC theme) ─────────────────────────────────────
_SKIN = "#e8c4a0"
_SKIN_DARK = "#c9a07a"
_SKIN_SHADOW = "#b08860"
_EYE_WHITE = "#f0efed"
_IRIS = "#4a9bd9"
_IRIS_DARK = "#2a6b9f"
_PUPIL = "#0a0a0a"
_BROW = "#3a2a1a"
_LASH = "#2a1a0a"
_LIP = "#cc7a7a"
_LIP_DARK = "#aa5555"
_NOSE = "#c9a580"
_HAIR = "#2a1a0a"

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


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ═════════════════════════════════════════════════════════════════════
# Vector Face Engine — draws everything on Canvas
# ═════════════════════════════════════════════════════════════════════
class _VectorFace:
    """Draws a complete animated face using Canvas primitives."""

    def __init__(self) -> None:
        # Animation state (set by OverwatchGUI)
        self.head_x = 0.0
        self.head_y = 0.0
        self.head_tilt = 0.0       # degrees
        self.breath_scale = 1.0

        # Eyes
        self.eyelid = 0.0          # 0=open, 1=fully closed
        self.gaze_x = 0.0         # -1 to 1
        self.gaze_y = 0.0         # -1 to 1

        # Mouth
        self.mouth_open = 0.0     # 0=closed, 1=wide open
        self.mouth_smile = 0.0    # 0=neutral, 1=full smile
        self.mouth_width = 1.0    # multiplier

        # Eyebrows
        self.brow_left = 0.0      # -1=furrowed, 0=neutral, 1=raised
        self.brow_right = 0.0

        # Mode for glow
        self.mode = "idle"

    def draw(self, cv: tk.Canvas, cx: float, cy: float) -> None:
        """Draw the complete face centered at (cx, cy)."""
        # Apply head offset and breathing
        fx = cx + self.head_x
        fy = cy + self.head_y
        s = self.breath_scale

        # ── Head shape ────────────────────────────────────────────────
        hw = 85 * s  # half-width
        hh = 105 * s  # half-height

        # Head shadow (offset)
        cv.create_oval(
            fx - hw + 3, fy - hh + 3,
            fx + hw + 3, fy + hh + 3,
            fill="#0a0e14", outline="", width=0
        )

        # Main head
        cv.create_oval(
            fx - hw, fy - hh, fx + hw, fy + hh,
            fill=_SKIN, outline=_SKIN_DARK, width=2
        )

        # Face shadow (lower half)
        jaw_y = fy + hh * 0.35
        cv.create_oval(
            fx - hw * 0.92, jaw_y,
            fx + hw * 0.92, fy + hh * 0.95,
            fill=_SKIN_DARK, outline="", width=0
        )

        # ── Hair (top of head) ────────────────────────────────────────
        hair_top = fy - hh * 1.02
        hair_bot = fy - hh * 0.45
        cv.create_arc(
            fx - hw * 1.08, hair_top,
            fx + hw * 1.08, hair_bot + (hair_bot - hair_top),
            start=0, extent=180,
            fill=_HAIR, outline=_HAIR, width=0
        )
        # Side hair strands
        for side in (-1, 1):
            sx = fx + side * hw * 0.95
            cv.create_oval(
                sx - 12 * s, fy - hh * 0.65,
                sx + 10 * s, fy - hh * 0.05,
                fill=_HAIR, outline="", width=0
            )

        # ── Ears ──────────────────────────────────────────────────────
        for side in (-1, 1):
            ex = fx + side * hw * 0.96
            cv.create_oval(
                ex - 8 * s, fy - 12 * s,
                ex + 8 * s, fy + 18 * s,
                fill=_SKIN, outline=_SKIN_DARK, width=1
            )

        # ── Eyes ──────────────────────────────────────────────────────
        eye_y = fy - hh * 0.12
        eye_spacing = hw * 0.48
        eye_w = 22 * s
        eye_h = 16 * s

        for side in (-1, 1):
            ex = fx + side * eye_spacing

            # White (sclera)
            cv.create_oval(
                ex - eye_w, eye_y - eye_h,
                ex + eye_w, eye_y + eye_h,
                fill=_EYE_WHITE, outline="#d0d0d0", width=1
            )

            # Iris
            iris_r = 10 * s
            ix = ex + self.gaze_x * 5 * s
            iy = eye_y + self.gaze_y * 3 * s
            cv.create_oval(
                ix - iris_r, iy - iris_r,
                ix + iris_r, iy + iris_r,
                fill=_IRIS, outline=_IRIS_DARK, width=1
            )

            # Pupil
            pupil_r = 4.5 * s
            cv.create_oval(
                ix - pupil_r, iy - pupil_r,
                ix + pupil_r, iy + pupil_r,
                fill=_PUPIL, outline="", width=0
            )

            # Iris highlight (specular)
            hx = ix - 3 * s
            hy = iy - 3 * s
            hr = 2.5 * s
            cv.create_oval(
                hx - hr, hy - hr, hx + hr, hy + hr,
                fill="white", outline="", width=0
            )

            # ── Eyelid (slides down for blink) ────────────────────────
            lid_drop = self.eyelid * eye_h * 2.2
            if lid_drop > 0.5:
                # Upper eyelid
                lid_top = eye_y - eye_h - 4 * s
                lid_bot = eye_y - eye_h + lid_drop
                cv.create_rectangle(
                    ex - eye_w - 2, lid_top,
                    ex + eye_w + 2, lid_bot,
                    fill=_SKIN, outline="", width=0
                )
                # Lash line
                cv.create_line(
                    ex - eye_w, lid_bot,
                    ex + eye_w, lid_bot,
                    fill=_LASH, width=2
                )

            # Upper eyelid crease (always visible)
            crease_y = eye_y - eye_h - 2 * s
            cv.create_arc(
                ex - eye_w * 0.9, crease_y - 5 * s,
                ex + eye_w * 0.9, crease_y + 8 * s,
                start=0, extent=180,
                outline=_SKIN_SHADOW, width=1, style="arc"
            )

            # Lower lash line
            cv.create_arc(
                ex - eye_w, eye_y - eye_h * 0.3,
                ex + eye_w, eye_y + eye_h + 2 * s,
                start=180, extent=180,
                outline=_LASH, width=1, style="arc"
            )

        # ── Eyebrows ─────────────────────────────────────────────────
        for side, brow_val in [(-1, self.brow_left), (1, self.brow_right)]:
            bx = fx + side * eye_spacing
            by_base = eye_y - eye_h - 10 * s
            brow_lift = brow_val * 6 * s  # positive = raised
            by = by_base - brow_lift

            # Inner and outer points
            inner_x = bx - side * eye_w * 0.85
            outer_x = bx + side * eye_w * 1.0
            mid_x = bx + side * eye_w * 0.1

            # Arch height based on expression
            arch = 4 * s + brow_val * 3 * s

            points = [
                inner_x, by + 2 * s,
                mid_x, by - arch,
                outer_x, by + 1 * s,
            ]
            cv.create_line(
                *points, fill=_BROW, width=3, smooth=True, capstyle="round"
            )

        # ── Nose ──────────────────────────────────────────────────────
        nose_y = fy + hh * 0.08
        # Bridge
        cv.create_line(
            fx - 2 * s, fy - hh * 0.05,
            fx - 3 * s, nose_y,
            fill=_NOSE, width=1, smooth=True
        )
        # Nostrils
        cv.create_arc(
            fx - 8 * s, nose_y - 3 * s,
            fx + 1 * s, nose_y + 5 * s,
            start=200, extent=140,
            outline=_NOSE, width=1, style="arc"
        )
        cv.create_arc(
            fx - 1 * s, nose_y - 3 * s,
            fx + 8 * s, nose_y + 5 * s,
            start=200, extent=140,
            outline=_NOSE, width=1, style="arc"
        )

        # ── Mouth ────────────────────────────────────────────────────
        mouth_y = fy + hh * 0.32
        mouth_w = 28 * s * self.mouth_width
        open_h = self.mouth_open * 18 * s
        smile_curve = self.mouth_smile * 6 * s

        if open_h < 1.5:
            # Closed mouth — single bezier line with optional smile
            points = [
                fx - mouth_w, mouth_y + smile_curve * 0.3,
                fx - mouth_w * 0.4, mouth_y - smile_curve * 0.5,
                fx, mouth_y - smile_curve * 0.2,
                fx + mouth_w * 0.4, mouth_y - smile_curve * 0.5,
                fx + mouth_w, mouth_y + smile_curve * 0.3,
            ]
            cv.create_line(
                *points, fill=_LIP, width=2.5, smooth=True, capstyle="round"
            )
        else:
            # Open mouth — filled oval opening
            cv.create_oval(
                fx - mouth_w * 0.75, mouth_y - open_h * 0.4,
                fx + mouth_w * 0.75, mouth_y + open_h * 0.9,
                fill="#2a0a0a", outline=_LIP_DARK, width=2
            )

            # Teeth hint (top)
            if open_h > 5:
                teeth_h = min(open_h * 0.3, 6 * s)
                cv.create_rectangle(
                    fx - mouth_w * 0.5, mouth_y - open_h * 0.3,
                    fx + mouth_w * 0.5, mouth_y - open_h * 0.3 + teeth_h,
                    fill="#f0ede8", outline="", width=0
                )

            # Upper lip
            upper_pts = [
                fx - mouth_w * 0.8, mouth_y,
                fx - mouth_w * 0.3, mouth_y - open_h * 0.5,
                fx, mouth_y - open_h * 0.45 - 2 * s,
                fx + mouth_w * 0.3, mouth_y - open_h * 0.5,
                fx + mouth_w * 0.8, mouth_y,
            ]
            cv.create_line(
                *upper_pts, fill=_LIP, width=2, smooth=True
            )

            # Lower lip
            lower_pts = [
                fx - mouth_w * 0.7, mouth_y + open_h * 0.2,
                fx - mouth_w * 0.2, mouth_y + open_h * 0.8,
                fx, mouth_y + open_h * 0.9,
                fx + mouth_w * 0.2, mouth_y + open_h * 0.8,
                fx + mouth_w * 0.7, mouth_y + open_h * 0.2,
            ]
            cv.create_line(
                *lower_pts, fill=_LIP, width=2.5, smooth=True
            )

        # Philtrum (small line above lip center)
        cv.create_line(
            fx, mouth_y - 10 * s,
            fx, mouth_y - 5 * s,
            fill=_NOSE, width=1
        )


# ═════════════════════════════════════════════════════════════════════
# Main GUI
# ═════════════════════════════════════════════════════════════════════
class OverwatchGUI(ctk.CTk):
    """PMC Overwatch — live vector face GUI."""

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
        self._face = _VectorFace()

        # Blink
        self._blink_timer = 0.0
        self._blink_cd = random.uniform(2.5, 5.5)
        self._blink_stage = 0  # 0=open, 1=closing, 2=closed, 3=opening
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

        # Gaze wander
        self._gaze_target_x = 0.0
        self._gaze_target_y = 0.0
        self._gaze_timer = 0.0
        self._gaze_cd = random.uniform(1.5, 4.0)

        # Breathing
        self._breath_phase = 0.0

        # Speaking — amplitude driven
        self._amplitude = 0.0
        self._amplitude_target = 0.0

        # Emotion overlay
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
        logger.info("Vector face GUI initialized")

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
            ar = 110  # glow radius
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

        # ── Draw the vector face ──────────────────────────────────────
        self._face.draw(cv, cx, cy)

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
        if self._blink_stage == 0:  # Open — waiting
            if self._blink_timer >= self._blink_cd:
                self._blink_stage = 1
                self._blink_dur = 0.0
                self._double_blink = random.random() < 0.15
                self._double_blink_count = 0
                # Start closing
        elif self._blink_stage == 1:  # Half closing
            self._blink_dur += dt
            self._face.eyelid = _lerp(self._face.eyelid, 0.5, 0.4)
            if self._blink_dur >= 0.05:
                self._blink_stage = 2
                self._blink_dur = 0.0
        elif self._blink_stage == 2:  # Fully closed
            self._blink_dur += dt
            self._face.eyelid = _lerp(self._face.eyelid, 1.0, 0.5)
            if self._blink_dur >= 0.07:
                self._blink_stage = 3
                self._blink_dur = 0.0
        elif self._blink_stage == 3:  # Opening
            self._blink_dur += dt
            self._face.eyelid = _lerp(self._face.eyelid, 0.0, 0.35)
            if self._blink_dur >= 0.06:
                self._face.eyelid = 0.0
                if self._double_blink and self._double_blink_count == 0:
                    self._double_blink_count = 1
                    self._blink_stage = 0
                    self._blink_cd = random.uniform(0.15, 0.3)
                else:
                    self._blink_stage = 0
                    self._blink_timer = 0.0
                    self._blink_cd = random.uniform(2.5, 5.5)

        # ── Head motion (visible wander) ──────────────────────────────
        self._head_timer += dt
        if self._head_timer >= self._head_cd:
            self._head_timer = 0.0
            self._head_cd = random.uniform(1.0, 2.5)
            self._head_target_x = random.uniform(-8.0, 8.0)
            self._head_target_y = random.uniform(-5.0, 5.0)
        self._head_x = _lerp(self._head_x, self._head_target_x, 0.05)
        self._head_y = _lerp(self._head_y, self._head_target_y, 0.05)

        # ── Gaze wander ──────────────────────────────────────────────
        self._gaze_timer += dt
        if self._gaze_timer >= self._gaze_cd:
            self._gaze_timer = 0.0
            self._gaze_cd = random.uniform(1.5, 4.0)
            self._gaze_target_x = random.uniform(-0.6, 0.6)
            self._gaze_target_y = random.uniform(-0.3, 0.3)
        self._face.gaze_x = _lerp(self._face.gaze_x, self._gaze_target_x, 0.04)
        self._face.gaze_y = _lerp(self._face.gaze_y, self._gaze_target_y, 0.04)

        # ── Breathing ────────────────────────────────────────────────
        self._breath_phase += dt * 1.1
        breath_y = math.sin(self._breath_phase) * 4.0
        breath_scale = 1.0 + math.sin(self._breath_phase * 0.5) * 0.015

        # Apply to face
        self._face.head_x = self._head_x
        self._face.head_y = self._head_y + breath_y
        self._face.breath_scale = breath_scale

        # ── Mode-specific ────────────────────────────────────────────
        if mode == "speaking":
            # Amplitude-driven mouth
            self._amplitude += (self._amplitude_target - self._amplitude) * 0.4
            self._face.mouth_open = self._amplitude
            # Subtle smile when speaking
            self._face.mouth_smile = _lerp(self._face.mouth_smile, 0.2, 0.05)
            self._face.brow_left = _lerp(self._face.brow_left, 0.1, 0.03)
            self._face.brow_right = _lerp(self._face.brow_right, 0.1, 0.03)

        elif mode == "thinking":
            self._face.mouth_open = _lerp(self._face.mouth_open, 0.0, 0.1)
            self._face.mouth_smile = _lerp(self._face.mouth_smile, 0.0, 0.05)
            # Thoughtful: slight brow furrow, gaze up
            self._face.brow_left = _lerp(self._face.brow_left, -0.3, 0.03)
            self._face.brow_right = _lerp(self._face.brow_right, 0.3, 0.03)

        elif mode == "listening":
            self._face.mouth_open = _lerp(self._face.mouth_open, 0.0, 0.1)
            # Attentive: slight brow raise
            self._face.mouth_smile = _lerp(self._face.mouth_smile, 0.15, 0.03)
            self._face.brow_left = _lerp(self._face.brow_left, 0.3, 0.03)
            self._face.brow_right = _lerp(self._face.brow_right, 0.3, 0.03)

        else:  # idle
            self._face.mouth_open = _lerp(self._face.mouth_open, 0.0, 0.08)
            self._face.mouth_smile = _lerp(self._face.mouth_smile, 0.05, 0.02)
            self._face.brow_left = _lerp(self._face.brow_left, 0.0, 0.02)
            self._face.brow_right = _lerp(self._face.brow_right, 0.0, 0.02)

        # ── Emotion overlay ──────────────────────────────────────────
        if self._emotion == "happy":
            self._face.mouth_smile = _lerp(self._face.mouth_smile, 0.8, 0.06)
            self._face.brow_left = _lerp(self._face.brow_left, 0.4, 0.04)
            self._face.brow_right = _lerp(self._face.brow_right, 0.4, 0.04)
            self._emotion_timer += dt
            if self._emotion_timer > 3.0:
                self._emotion = "neutral"
                self._emotion_timer = 0.0
        elif self._emotion == "curious":
            self._face.brow_left = _lerp(self._face.brow_left, 0.6, 0.04)
            self._face.brow_right = _lerp(self._face.brow_right, -0.2, 0.04)
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

        self._face.mode = mode

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
        ctk.CTkLabel(inner, text="v0.17.0", font=ctk.CTkFont(size=11),
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
        for _ in range(30):
            app.set_amplitude(_r.uniform(0.0, 1.0))
            time.sleep(0.08)
        app.set_amplitude(0.0)
        time.sleep(1)
        app.after(0, lambda: app.set_emotion("happy"))
        time.sleep(2)
        app.after(0, lambda: app.set_vis_mode("thinking"))
        time.sleep(2)
        app.after(0, lambda: app.set_vis_mode("idle"))
        time.sleep(2)
        app.after(0, app._on_close)
    threading.Thread(target=_demo, daemon=True).start()
    app.mainloop()
    print("VECTOR FACE DEMO PASSED")
