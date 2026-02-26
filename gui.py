"""
PMC Overwatch GUI — Vector-animated character face.

Every facial feature is drawn on the Tkinter Canvas using geometric shapes
and animated independently with smooth linear interpolation (lerp):
  * Head outline with subtle tilt / breathing
  * Eyes: sclera, iris, pupil — with gaze tracking, dilation, blink
  * Eyebrows: arcs that raise/furrow per expression
  * Mouth: bezier-approximated arcs for 5 viseme shapes
  * Hair: layered polygon with sway
  * Neck / shoulders silhouette
  * State-reactive glow ring + voice bars

24 FPS render loop. All motion uses exponential smoothing for natural feel.
"""

import logging
import math
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

# Sizing
_CANVAS_W = 400
_CANVAS_H = 420
_FPS = 24

# Voice bars
_N_BARS = 31
_BAR_W = 3
_BAR_GAP = 1
_BAR_MAX_H = 14

# Glow per state
_GLOW = {"idle": _MUTED, "listening": _GREEN, "thinking": _AMBER, "speaking": _CYAN}
_GLOW_RGB = {
    "idle": (61, 68, 80), "listening": (0, 210, 106),
    "thinking": (255, 165, 2), "speaking": (0, 210, 255),
}

# ── Skin / feature colours ───────────────────────────────────────────
_SKIN = "#f0c8a0"
_SKIN_SHADOW = "#d4a87a"
_SKIN_DARK = "#c0906a"
_LIP = "#c06070"
_LIP_INNER = "#802030"
_EYE_WHITE = "#f8f4f0"
_IRIS = "#3a9a5e"
_PUPIL = "#0a0a0a"
_BROW = "#4a3020"
_HAIR = "#1a1a30"
_HAIR_HIGHLIGHT = "#2a2a48"
_LASH = "#201010"


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class _FaceState:
    """Interpolatable face state — all values 0-1 or in pixels."""
    __slots__ = (
        "head_x", "head_y", "head_tilt",
        "eye_open_l", "eye_open_r",
        "gaze_x", "gaze_y",
        "pupil_size",
        "brow_l", "brow_r",
        "mouth_open", "mouth_wide", "mouth_smile",
    )

    def __init__(self):
        self.head_x = 0.0       # px offset
        self.head_y = 0.0       # px offset
        self.head_tilt = 0.0    # radians
        self.eye_open_l = 1.0   # 0=closed, 1=open
        self.eye_open_r = 1.0
        self.gaze_x = 0.0      # -1 to 1
        self.gaze_y = 0.0      # -1 to 1
        self.pupil_size = 1.0   # multiplier
        self.brow_l = 0.0       # -1=frown, 0=neutral, 1=raised
        self.brow_r = 0.0
        self.mouth_open = 0.0   # 0=closed, 1=wide open
        self.mouth_wide = 0.5   # 0=narrow, 1=wide spread
        self.mouth_smile = 0.3  # -1=frown, 0=neutral, 1=smile

    def lerp_to(self, target: "_FaceState", speed: float = 0.15) -> None:
        for attr in self.__slots__:
            cur = getattr(self, attr)
            tgt = getattr(target, attr)
            setattr(self, attr, _lerp(cur, tgt, speed))


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

        # Animation state
        self._mode = "idle"
        self._phase = 0.0
        self._anim_id: Optional[str] = None
        self._pulse_id: Optional[str] = None
        self._pulse_vis = True
        self._dot_color = _MUTED

        # Face animation
        self._face = _FaceState()
        self._target = _FaceState()

        # Blink
        self._blink_cd = random.uniform(2.5, 5.0)
        self._blink_timer = 0.0
        self._blinking = False
        self._blink_dur = 0.0

        # Speaking mouth
        self._talk_timer = 0.0
        self._talk_phase = 0.0

        # Idle micro-motion
        self._idle_timer = 0.0
        self._gaze_target_x = 0.0
        self._gaze_target_y = 0.0
        self._gaze_change_cd = random.uniform(1.5, 4.0)

        # Voice bars
        self._bar_target = [0.0] * _N_BARS
        self._bar_current = [0.0] * _N_BARS

        self._build_header()
        self._build_agent()
        self._build_log()
        self._build_footer()
        self._start_anim()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        logger.info("Vector-animated face GUI initialized")

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

    # ══ FACE DRAWING ═════════════════════════════════════════════════
    def _draw_face(self) -> None:
        cv = self._cv
        cv.delete("all")
        f = self._face
        cx = _CANVAS_W // 2 + f.head_x
        cy = _CANVAS_H // 2 - 20 + f.head_y
        glow_c = _GLOW.get(self._mode, _MUTED)
        glow_rgb = _GLOW_RGB.get(self._mode, (61, 68, 80))

        # ── Glow ring (behind face) ──────────────────────────────────
        pulse = (math.sin(self._phase * 0.7) + 1) * 0.5
        if self._mode != "idle":
            for i in range(3):
                r = 108 + i * 10 + pulse * 4
                a = max(0, 40 - i * 14)
                rc = int(glow_rgb[0] * a / 255)
                gc = int(glow_rgb[1] * a / 255)
                bc = int(glow_rgb[2] * a / 255)
                c = f"#{max(10, rc):02x}{max(14, gc):02x}{max(20, bc):02x}"
                cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=c, width=2)

        # ── Neck ─────────────────────────────────────────────────────
        nw, nh = 40, 30
        cv.create_rectangle(cx - nw, cy + 78, cx + nw, cy + 78 + nh,
                            fill=_SKIN_SHADOW, outline="")
        # Shoulders silhouette
        cv.create_arc(cx - 100, cy + 80, cx + 100, cy + 160,
                      start=0, extent=180, fill=_CARD, outline="")

        # ── Head shape (oval) ────────────────────────────────────────
        head_rx, head_ry = 85, 100
        cv.create_oval(cx - head_rx, cy - head_ry, cx + head_rx, cy + head_ry,
                       fill=_SKIN, outline=_SKIN_SHADOW, width=2)

        # Slight jaw shadow
        cv.create_arc(cx - 72, cy + 20, cx + 72, cy + 100,
                      start=200, extent=140, fill=_SKIN_SHADOW, outline="")

        # Cheek blush (subtle)
        for side in [-1, 1]:
            bx = cx + side * 55
            by = cy + 22
            cv.create_oval(bx - 14, by - 8, bx + 14, by + 8,
                           fill="#f0b8a0", outline="")

        # ── Hair (back layer) ────────────────────────────────────────
        hair_sway = math.sin(self._phase * 0.3) * 2
        # Main hair mass
        cv.create_arc(cx - 90, cy - 110, cx + 90, cy - 10,
                      start=0, extent=180, fill=_HAIR, outline="")
        # Side bangs
        for side in [-1, 1]:
            pts = [
                cx + side * 80, cy - 60,
                cx + side * 92 + hair_sway, cy - 10,
                cx + side * 88 + hair_sway, cy + 40,
                cx + side * 78, cy + 60,
                cx + side * 72, cy + 20,
                cx + side * 75, cy - 30,
            ]
            cv.create_polygon(pts, fill=_HAIR, outline="", smooth=True)

        # Fringe / bangs across forehead
        fringe_pts = [
            cx - 75, cy - 70,
            cx - 50, cy - 85,
            cx - 20, cy - 72 + hair_sway * 0.5,
            cx, cy - 78,
            cx + 20, cy - 74 + hair_sway * 0.3,
            cx + 50, cy - 86,
            cx + 75, cy - 70,
            cx + 80, cy - 55,
            cx, cy - 58,
            cx - 80, cy - 55,
        ]
        cv.create_polygon(fringe_pts, fill=_HAIR, outline="", smooth=True)

        # Hair highlights
        cv.create_arc(cx - 60, cy - 100, cx + 20, cy - 50,
                      start=20, extent=140, outline=_HAIR_HIGHLIGHT, width=2, style="arc")

        # ── Eyes ─────────────────────────────────────────────────────
        eye_y = cy - 12
        eye_spacing = 32

        for side_idx, side in enumerate([-1, 1]):
            ex = cx + side * eye_spacing
            eye_open = f.eye_open_l if side == -1 else f.eye_open_r

            # Eye socket shadow
            cv.create_oval(ex - 22, eye_y - 14, ex + 22, eye_y + 14,
                           fill=_SKIN_SHADOW, outline="")

            if eye_open > 0.08:
                # Sclera (white)
                ey_h = 12 * eye_open
                cv.create_oval(ex - 18, eye_y - ey_h, ex + 18, eye_y + ey_h,
                               fill=_EYE_WHITE, outline="#d0ccc8", width=1)

                # Iris
                iris_r = 9 * min(eye_open, 0.85)
                gx = f.gaze_x * 5
                gy = f.gaze_y * 3
                cv.create_oval(ex + gx - iris_r, eye_y + gy - iris_r,
                               ex + gx + iris_r, eye_y + gy + iris_r,
                               fill=_IRIS, outline="#2a7a4a", width=1)

                # Pupil
                pr = 4 * f.pupil_size * min(eye_open, 0.85)
                cv.create_oval(ex + gx - pr, eye_y + gy - pr,
                               ex + gx + pr, eye_y + gy + pr,
                               fill=_PUPIL, outline="")

                # Catchlight (reflection dot)
                cv.create_oval(ex + gx + 2, eye_y + gy - 4,
                               ex + gx + 5, eye_y + gy - 1,
                               fill="white", outline="")

                # Upper eyelid line
                cv.create_arc(ex - 18, eye_y - 13, ex + 18, eye_y + 5,
                              start=20, extent=140, outline=_LASH, width=2, style="arc")
            else:
                # Closed eye — just a line
                cv.create_line(ex - 16, eye_y, ex + 16, eye_y,
                               fill=_LASH, width=2, smooth=True)

        # ── Eyebrows ─────────────────────────────────────────────────
        for side, brow_val in [(-1, f.brow_l), (1, f.brow_r)]:
            bx = cx + side * 32
            by = eye_y - 22 - brow_val * 6
            bw = 20
            # Arch shape
            inner_y = by + 2 + brow_val * 2
            outer_y = by - 1
            pts = [
                bx - side * bw, inner_y,
                bx, outer_y - 3,
                bx + side * bw * 0.3, outer_y,
            ]
            cv.create_line(pts, fill=_BROW, width=3, smooth=True)

        # ── Nose ─────────────────────────────────────────────────────
        nose_y = cy + 12
        cv.create_line(cx - 2, cy - 4, cx - 4, nose_y, fill=_SKIN_DARK, width=1)
        cv.create_oval(cx - 6, nose_y - 3, cx + 6, nose_y + 3,
                       outline=_SKIN_DARK, width=1)

        # ── Mouth ────────────────────────────────────────────────────
        mouth_y = cy + 38
        mouth_w = 22 + f.mouth_wide * 10
        open_h = f.mouth_open * 14
        smile = f.mouth_smile

        if open_h < 1.5:
            # Closed mouth — curved line
            smile_curve = smile * 4
            pts = [
                cx - mouth_w, mouth_y,
                cx - mouth_w * 0.5, mouth_y + smile_curve,
                cx, mouth_y + smile_curve * 1.2,
                cx + mouth_w * 0.5, mouth_y + smile_curve,
                cx + mouth_w, mouth_y,
            ]
            cv.create_line(pts, fill=_LIP, width=2, smooth=True)
        else:
            # Open mouth
            # Upper lip
            cv.create_arc(cx - mouth_w, mouth_y - open_h * 0.3,
                          cx + mouth_w, mouth_y + open_h * 0.8,
                          start=0, extent=180, fill=_LIP_INNER, outline=_LIP, width=2)
            # Lower lip
            cv.create_arc(cx - mouth_w * 0.9, mouth_y - open_h * 0.2,
                          cx + mouth_w * 0.9, mouth_y + open_h,
                          start=180, extent=180, fill=_LIP, outline=_LIP, width=1)
            # Teeth hint (when wide open)
            if open_h > 6:
                teeth_w = mouth_w * 0.7
                cv.create_rectangle(cx - teeth_w, mouth_y,
                                    cx + teeth_w, mouth_y + min(open_h * 0.3, 4),
                                    fill="#f0ece8", outline="")

        # ── Headset / tactical gear ──────────────────────────────────
        # Earpiece
        for side in [-1, 1]:
            hx = cx + side * 82
            hy = cy - 5
            cv.create_oval(hx - 10, hy - 14, hx + 10, hy + 14,
                           fill="#303038", outline="#404048", width=2)
            # Band across top
        cv.create_arc(cx - 82, cy - 96, cx + 82, cy - 20,
                      start=0, extent=180, outline="#383840", width=3, style="arc")
        # Mic boom
        mic_pts = [
            cx + 72, cy + 2,
            cx + 60, cy + 32,
            cx + 35, cy + 42,
        ]
        cv.create_line(mic_pts, fill="#404048", width=3, smooth=True)
        cv.create_oval(cx + 30, cy + 38, cx + 40, cy + 48,
                       fill="#505058", outline="#606068")

        # ── Speaking: ripple waves ────────────────────────────────────
        if self._mode == "speaking":
            ring_cx = _CANVAS_W // 2
            ring_cy = _CANVAS_H // 2 - 20
            for wi in range(3):
                wp = self._phase * 2.0 + wi * 2.0
                wr = 110 + (wp % 4.0) * 12
                wa = max(0, 1.0 - (wp % 4.0) / 4.0)
                if wa > 0.05:
                    rv = int(glow_rgb[0] * wa * 0.4)
                    gv = int(glow_rgb[1] * wa * 0.4)
                    bv = int(glow_rgb[2] * wa * 0.4)
                    wc = f"#{max(10, rv):02x}{max(14, gv):02x}{max(20, bv):02x}"
                    cv.create_oval(ring_cx - wr, ring_cy - wr,
                                   ring_cx + wr, ring_cy + wr,
                                   outline=wc, width=1)

        # ── Voice bars ────────────────────────────────────────────────
        total_w = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        bx = _CANVAS_W // 2 - total_w // 2
        by = _CANVAS_H - 22
        for i in range(_N_BARS):
            h = max(1, int(self._bar_current[i] * _BAR_MAX_H))
            x = bx + i * (_BAR_W + _BAR_GAP)
            cv.create_rectangle(x, by - h, x + _BAR_W, by, fill=glow_c, outline="")
            cv.create_rectangle(x, by, x + _BAR_W, by + h, fill=glow_c, outline="")

    # ══ ANIMATION LOGIC ══════════════════════════════════════════════
    def _update_targets(self, dt: float) -> None:
        t = self._target
        mode = self._mode

        # ── Blink ────────────────────────────────────────────────────
        self._blink_timer += dt
        if self._blinking:
            self._blink_dur += dt
            if self._blink_dur < 0.07:
                t.eye_open_l = t.eye_open_r = 0.0
            elif self._blink_dur < 0.14:
                t.eye_open_l = t.eye_open_r = 0.3
            else:
                self._blinking = False
                self._blink_dur = 0.0
                self._blink_timer = 0.0
                self._blink_cd = random.uniform(2.5, 5.5)
                t.eye_open_l = t.eye_open_r = 1.0
        elif self._blink_timer >= self._blink_cd:
            self._blinking = True
            self._blink_dur = 0.0

        # ── Gaze micro-movement ──────────────────────────────────────
        self._idle_timer += dt
        if self._idle_timer >= self._gaze_change_cd:
            self._idle_timer = 0
            self._gaze_change_cd = random.uniform(1.5, 4.0)
            if mode == "thinking":
                self._gaze_target_x = random.uniform(-0.6, 0.6)
                self._gaze_target_y = random.uniform(-0.8, -0.2)
            elif mode == "listening":
                self._gaze_target_x = random.uniform(-0.3, 0.3)
                self._gaze_target_y = random.uniform(-0.2, 0.2)
            else:
                self._gaze_target_x = random.uniform(-0.4, 0.4)
                self._gaze_target_y = random.uniform(-0.3, 0.3)

        t.gaze_x = self._gaze_target_x
        t.gaze_y = self._gaze_target_y

        # ── Head breathing / sway ────────────────────────────────────
        t.head_x = math.sin(self._phase * 0.25) * 2.5
        t.head_y = math.sin(self._phase * 0.15) * 2.0

        # ── Mode-specific expressions ────────────────────────────────
        if mode == "speaking":
            self._talk_timer += dt
            self._talk_phase += dt * 10  # Fast oscillation
            # Viseme cycle: closed → half → wide → half → closed
            wave = (math.sin(self._talk_phase) + 1) * 0.5
            noise = random.uniform(0, 0.3)
            t.mouth_open = wave * 0.8 + noise * 0.2
            t.mouth_wide = 0.4 + wave * 0.4
            t.mouth_smile = 0.1
            t.brow_l = 0.1 + wave * 0.15
            t.brow_r = 0.1 + wave * 0.15
            t.pupil_size = 0.9

        elif mode == "thinking":
            t.mouth_open = 0.0
            t.mouth_wide = 0.4
            t.mouth_smile = -0.1
            t.brow_l = 0.5  # Raised
            t.brow_r = 0.3
            t.pupil_size = 0.8

        elif mode == "listening":
            t.mouth_open = 0.0
            t.mouth_wide = 0.5
            t.mouth_smile = 0.2
            t.brow_l = 0.1
            t.brow_r = 0.1
            t.pupil_size = 1.1

        else:  # idle
            t.mouth_open = 0.0
            t.mouth_wide = 0.5
            t.mouth_smile = 0.15
            t.brow_l = 0.0
            t.brow_r = 0.0
            t.pupil_size = 1.0

        # Smooth interpolation
        speed = 0.25 if mode == "speaking" else 0.12
        self._face.lerp_to(t, speed)

    def _start_anim(self) -> None:
        self._tick()

    def _tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        dt = 1.0 / _FPS
        self._phase += dt

        self._update_targets(dt)

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

        self._draw_face()
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
        ctk.CTkLabel(inner, text="v12.0", font=ctk.CTkFont(size=11),
                     text_color=_MUTED).pack(side="right")

    # ══ PUBLIC API (thread-safe) ═════════════════════════════════════
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
