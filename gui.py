"""
PMC Overwatch GUI — procedurally animated AI agent.

Design:
  • Animated silhouette agent with glowing eyes and circuit lines
  • Pulsing energy rings that react to listening/speaking/thinking states
  • Dynamic voice-reactive waveform bars
  • Orbiting data particles for liveliness
  • State-driven colour scheme for all effects
  • No external image assets required — runs on any machine
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
_PURPLE = "#a855f7"
_TEXT = "#e6edf3"
_TEXT2 = "#7b8794"
_MUTED = "#3d4450"
_BORDER = "#252b35"

# Agent sizing
_CANVAS_W = 340
_CANVAS_H = 420
_FPS = 24

# Voice bars
_N_BARS = 21
_BAR_W = 3
_BAR_GAP = 2
_BAR_MAX_H = 30

# Glow per state
_GLOW = {
    "idle": _MUTED,
    "listening": _GREEN,
    "thinking": _AMBER,
    "speaking": _CYAN,
}

# Particles
_N_PARTICLES = 24


class _Particle:
    __slots__ = ("angle", "radius", "speed", "size", "alpha", "drift")

    def __init__(self):
        self.angle = random.uniform(0, 2 * math.pi)
        self.radius = random.uniform(120, 170)
        self.speed = random.uniform(0.004, 0.015)
        self.size = random.uniform(1.2, 3.0)
        self.alpha = random.uniform(0.3, 0.8)
        self.drift = random.uniform(-0.3, 0.3)


class OverwatchGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PMC Overwatch")
        self.geometry("820x800")
        self.minsize(620, 600)
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

        # Voice bar levels
        self._bar_target = [0.0] * _N_BARS
        self._bar_current = [0.0] * _N_BARS

        # Particles
        self._particles = [_Particle() for _ in range(_N_PARTICLES)]

        # Circuit line segments (pre-generated for consistency)
        self._circuit_lines = self._gen_circuit_lines()

        self._build_header()
        self._build_agent()
        self._build_log()
        self._build_footer()
        self._start_anim()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    @staticmethod
    def _gen_circuit_lines():
        """Generate random circuit-like line segments on the agent face."""
        lines = []
        random.seed(42)  # Deterministic so it looks the same every run
        for _ in range(8):
            # Start from center-ish area
            sx = random.uniform(-25, 25)
            sy = random.uniform(-35, 35)
            points = [(sx, sy)]
            for _ in range(random.randint(2, 4)):
                dx = random.choice([-1, 1]) * random.uniform(8, 20)
                dy = random.choice([-1, 1]) * random.uniform(5, 15)
                points.append((points[-1][0] + dx, points[-1][1] + dy))
            lines.append(points)
        random.seed()  # Reset seed
        return lines

    # ══════════════════════════════════════════════════════════════════
    #  HEADER
    # ══════════════════════════════════════════════════════════════════
    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, corner_radius=16, fg_color=_CARD,
                           border_width=1, border_color=_BORDER)
        hdr.pack(fill="x", padx=20, pady=(18, 0))
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=14)

        logo = ctk.CTkFrame(inner, fg_color="transparent")
        logo.pack(side="left")

        # Use a cross-platform font for the logo
        _font = "Segoe UI" if platform.system() == "Windows" else "SF Pro Display"
        ctk.CTkLabel(logo, text="PMC Overwatch",
                     font=ctk.CTkFont(family=_font, size=24, weight="bold"),
                     text_color=_TEXT).pack(anchor="w")
        ctk.CTkLabel(logo, text="AI Companion  |  Escape from Tarkov",
                     font=ctk.CTkFont(size=11), text_color=_TEXT2).pack(anchor="w")

        self._btn = ctk.CTkButton(
            inner, text="Start", width=150, height=46,
            corner_radius=14, font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=_GREEN, hover_color=_GREEN_H, text_color="white",
            command=self._on_toggle)
        self._btn.pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  AGENT — procedurally animated AI entity
    # ══════════════════════════════════════════════════════════════════
    def _build_agent(self) -> None:
        self._cv = tk.Canvas(self, width=_CANVAS_W, height=_CANVAS_H,
                             bg=_BG, highlightthickness=0, bd=0)
        self._cv.pack(pady=(12, 0))

        self._av_status = ctk.CTkLabel(
            self, text="Offline",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=_MUTED)
        self._av_status.pack(pady=(4, 6))

    def _render(self) -> None:
        cv = self._cv
        cv.delete("all")
        cx = _CANVAS_W // 2
        cy = _CANVAS_H // 2 - 20

        # Breathing bob + micro-sway
        bob = math.sin(self._phase * 0.3) * 3
        sway = math.sin(self._phase * 0.15) * 2
        cy_f = cy + bob
        glow_c = _GLOW.get(self._mode, _MUTED)

        # ── Particles (orbit the agent) ──────────────────────────────
        for p in self._particles:
            p.angle += p.speed
            rad_mod = p.radius + math.sin(self._phase * 0.5 + p.angle * 2) * 8
            px = cx + sway + math.cos(p.angle) * rad_mod
            py = cy_f + math.sin(p.angle) * rad_mod * 0.8 + p.drift
            sz = p.size
            if self._mode == "speaking":
                sz *= 1.0 + (math.sin(self._phase * 2.5 + p.angle) + 1) * 0.5
            elif self._mode == "thinking":
                sz *= 1.0 + (math.sin(self._phase * 4 + p.angle * 3) + 1) * 0.2
            cv.create_oval(px - sz, py - sz, px + sz, py + sz,
                           fill=glow_c, outline="")

        # ── Energy rings ─────────────────────────────────────────────
        pulse = (math.sin(self._phase * 1.0) + 1) * 0.5

        for i, (base_r, w) in enumerate([(100, 1), (88, 2), (78, 1)]):
            r = base_r + pulse * (4 - i)
            # Draw dashed ring effect
            segments = 32
            for j in range(segments):
                if j % 3 == 0:
                    continue  # gap
                a1 = (j / segments) * 2 * math.pi + self._phase * (0.1 * (i + 1))
                a2 = ((j + 1) / segments) * 2 * math.pi + self._phase * (0.1 * (i + 1))
                x1 = cx + sway + math.cos(a1) * r
                y1 = cy_f + math.sin(a1) * r
                x2 = cx + sway + math.cos(a2) * r
                y2 = cy_f + math.sin(a2) * r
                cv.create_line(x1, y1, x2, y2, fill=glow_c, width=w)

        # ── Shoulders + Neck (drawn first, behind head) ─────────────────
        neck_w = 18
        neck_h = 25
        shoulder_w = 85
        shoulder_h = 30
        neck_top = cy_f + 38
        # Shoulders (rounded trapezoid)
        cv.create_polygon(
            cx + sway - neck_w, neck_top,
            cx + sway - shoulder_w, neck_top + neck_h + shoulder_h,
            cx + sway - shoulder_w + 15, neck_top + neck_h + shoulder_h + 10,
            cx + sway + shoulder_w - 15, neck_top + neck_h + shoulder_h + 10,
            cx + sway + shoulder_w, neck_top + neck_h + shoulder_h,
            cx + sway + neck_w, neck_top,
            fill="#1a2030", outline=glow_c, width=1, smooth=True
        )
        # Neck
        cv.create_rectangle(
            cx + sway - neck_w, neck_top,
            cx + sway + neck_w, neck_top + neck_h,
            fill="#1e2535", outline=""
        )

        # ── Head (oval, not circle — more realistic) ──────────────────
        head_w = 48
        head_h = 58
        head_cy = cy_f - 5
        # Face fill
        for ring in range(0, head_h, 2):
            frac = ring / head_h
            rr = int(28 + frac * 10)
            gg = int(32 + frac * 10)
            bb = int(42 + frac * 12)
            fill = f"#{rr:02x}{gg:02x}{bb:02x}"
            rx = head_w * (1 - (frac - 0.5)**2 * 0.3)
            ry = head_h - ring
            cv.create_oval(cx + sway - rx, head_cy - ry,
                           cx + sway + rx, head_cy + ry,
                           fill=fill, outline="")

        # Head outline
        cv.create_oval(cx + sway - head_w, head_cy - head_h,
                       cx + sway + head_w, head_cy + head_h,
                       outline=glow_c, width=2)

        # ── Hair (styled, swept to the side) ──────────────────────────
        hair_pts = [
            (-head_w - 5, -15), (-head_w + 2, -head_h + 5),
            (-head_w + 15, -head_h - 8), (-5, -head_h - 12),
            (10, -head_h - 10), (head_w - 5, -head_h - 5),
            (head_w + 3, -head_h + 10), (head_w + 5, -20),
            (head_w + 2, -5), (head_w - 3, 5),
            (head_w - 15, -head_h + 15), (0, -head_h + 5),
            (-head_w + 10, -head_h + 12), (-head_w - 3, -5),
        ]
        hair_coords = []
        for px, py in hair_pts:
            hair_coords.extend([cx + sway + px, head_cy + py])
        cv.create_polygon(*hair_coords, fill="#14191f", outline=glow_c,
                          width=1, smooth=True)

        # ── Ears (small hints on sides) ───────────────────────────────
        for side in [-1, 1]:
            ear_x = cx + sway + side * (head_w - 2)
            ear_y = head_cy - 5
            cv.create_oval(ear_x - 5, ear_y - 8, ear_x + 5, ear_y + 8,
                           fill="#1e2535", outline=glow_c, width=1)

        # ── Eyebrows ─────────────────────────────────────────────────
        brow_y = head_cy - 22
        for side in [-1, 1]:
            bx = cx + sway + side * 18
            cv.create_line(bx - 10, brow_y + side * 1,
                           bx + 10, brow_y - side * 1,
                           fill=glow_c, width=2)

        # ── Eyes (expressive, reactive) ───────────────────────────────
        eye_y = head_cy - 12
        eye_sep = 18
        eye_w = 11
        eye_h = 5

        # Blink effect (every ~5 seconds)
        blink = (math.sin(self._phase * 0.4) > 0.95)
        actual_eye_h = 1 if blink else eye_h

        for side in [-1, 1]:
            ex = cx + sway + side * eye_sep
            # Eye white
            cv.create_oval(ex - eye_w - 1, eye_y - actual_eye_h - 1,
                           ex + eye_w + 1, eye_y + actual_eye_h + 1,
                           fill="#1a2030", outline="")
            # Iris
            cv.create_oval(ex - eye_w, eye_y - actual_eye_h,
                           ex + eye_w, eye_y + actual_eye_h,
                           fill=glow_c, outline="")
            # Pupil
            pupil_r = 3 if not blink else 1
            cv.create_oval(ex - pupil_r, eye_y - pupil_r,
                           ex + pupil_r, eye_y + pupil_r,
                           fill="white", outline="")
            # Eye glow halo
            if self._mode != "idle":
                glow_r = eye_w + 6
                cv.create_oval(ex - glow_r, eye_y - glow_r,
                               ex + glow_r, eye_y + glow_r,
                               fill="", outline=glow_c, width=1)

        # ── Nose (subtle line) ────────────────────────────────────────
        nose_top = head_cy - 2
        nose_bot = head_cy + 10
        cv.create_line(cx + sway, nose_top, cx + sway - 3, nose_bot,
                       fill=glow_c, width=1)
        cv.create_line(cx + sway - 3, nose_bot, cx + sway + 3, nose_bot,
                       fill=glow_c, width=1)

        # ── Mouth (animated when speaking) ────────────────────────────
        mouth_y = head_cy + 20
        mouth_w = 14
        if self._mode == "speaking":
            # Animate mouth open/close
            mouth_open = abs(math.sin(self._phase * 4)) * 6
            cv.create_oval(cx + sway - mouth_w, mouth_y - mouth_open / 2,
                           cx + sway + mouth_w, mouth_y + mouth_open / 2 + 2,
                           fill="#0d1117", outline=glow_c, width=1)
        else:
            # Closed mouth (slight smile)
            cv.create_arc(cx + sway - mouth_w, mouth_y - 4,
                          cx + sway + mouth_w, mouth_y + 8,
                          start=200, extent=140,
                          style="arc", outline=glow_c, width=1)

        # ── Circuit lines (tech overlay on one cheek) ─────────────────
        circuit_alpha = 0.3 + pulse * 0.3
        for line_pts in self._circuit_lines:
            for k in range(len(line_pts) - 1):
                x1 = cx + sway + line_pts[k][0]
                y1 = cy_f + line_pts[k][1]
                x2 = cx + sway + line_pts[k + 1][0]
                y2 = cy_f + line_pts[k + 1][1]
                cv.create_line(x1, y1, x2, y2, fill=glow_c, width=1)
            # Node dots at endpoints
            for pt in line_pts:
                nx = cx + sway + pt[0]
                ny = cy_f + pt[1]
                cv.create_oval(nx - 1.5, ny - 1.5, nx + 1.5, ny + 1.5,
                               fill=glow_c, outline="")

        # ── Voice waveform bars (below agent) ─────────────────────────
        total_w = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        bx_start = cx - total_w // 2
        by_base = neck_top + neck_h + shoulder_h + 18

        for i in range(_N_BARS):
            h = max(2, int(self._bar_current[i] * _BAR_MAX_H))
            x = bx_start + i * (_BAR_W + _BAR_GAP)
            # Symmetric: bars go up AND down from center
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

        # Update bar targets based on state
        if self._mode == "speaking":
            for i in range(_N_BARS):
                center_w = 1.0 - abs(i - _N_BARS // 2) / (_N_BARS // 2) * 0.4
                self._bar_target[i] = max(0.12, random.uniform(0.2, 1.0) * center_w)
        elif self._mode == "thinking":
            for i in range(_N_BARS):
                wave = (math.sin(self._phase * 3.0 + i * 0.4) + 1) * 0.5
                self._bar_target[i] = wave * 0.3
        elif self._mode == "listening":
            for i in range(_N_BARS):
                self._bar_target[i] = 0.04 + (math.sin(self._phase * 0.5 + i * 0.25) + 1) * 0.04
        else:
            for i in range(_N_BARS):
                self._bar_target[i] = 0.0

        # Smooth interpolation
        for i in range(_N_BARS):
            diff = self._bar_target[i] - self._bar_current[i]
            self._bar_current[i] += diff * 0.3

        self._render()
        self._anim_id = self.after(1000 // _FPS, self._tick)

    # ══════════════════════════════════════════════════════════════════
    #  LOG
    # ══════════════════════════════════════════════════════════════════
    def _build_log(self) -> None:
        f = ctk.CTkFrame(self, corner_radius=16, fg_color=_CARD,
                         border_width=1, border_color=_BORDER)
        f.pack(fill="both", expand=True, padx=20, pady=10)
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(hdr, text="Activity Log",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=_TEXT2).pack(side="left")

        # Use a monospace font that exists on all platforms
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
                          border_width=1, border_color=_BORDER, height=44)
        ft.pack(fill="x", padx=20, pady=(0, 18))
        ft.pack_propagate(False)
        inner = ctk.CTkFrame(ft, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=10)
        self._dot = ctk.CTkLabel(inner, text="●", font=ctk.CTkFont(size=12),
                                 text_color=_MUTED, width=16)
        self._dot.pack(side="left", padx=(0, 6))
        self._status_lbl = ctk.CTkLabel(inner, text="Offline",
                                        font=ctk.CTkFont(size=12), text_color=_TEXT2)
        self._status_lbl.pack(side="left")
        ctk.CTkLabel(inner, text="v6.0", font=ctk.CTkFont(size=11),
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
            "idle": ("Offline", _MUTED),
            "listening": ("Listening...", _GREEN),
            "speaking": ("Speaking...", _CYAN),
            "thinking": ("Thinking...", _AMBER),
        }
        t, c = labels.get(mode, ("Offline", _MUTED))
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
        self._btn.configure(text="Start", fg_color=_GREEN, hover_color=_GREEN_H)
        self._do_status("Offline")

    def _on_toggle(self) -> None:
        self._is_running = not self._is_running
        if self._is_running:
            self._btn.configure(text="Stop", fg_color=_RED, hover_color=_RED_H)
            self._do_status("Starting...")
            self.log("Overwatch activated")
        else:
            self._btn.configure(text="Start", fg_color=_GREEN, hover_color=_GREEN_H)
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
