"""
PMC Overwatch GUI — dynamic animated avatar with continuous motion.

Design:
  * Pre-renders 24 animation frames from base avatar with PIL transforms:
    - Head tilt (left/right shift)
    - Breathing zoom (subtle scale)
    - Light temperature shifts (warm/cool tints)
    - Eye look direction shifts
  * Blink frames, speaking frames with mouth glow
  * Holographic scan-line overlay effect
  * Smooth 24fps animation with state-reactive effects
"""

import logging
import math
import platform
import random
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

# ── Avatar asset path ────────────────────────────────────────────────
_AVATAR_PATH = Path(__file__).parent / "assets" / "avatar.png"

# ── Palette ──────────────────────────────────────────────────────────
_BG = "#0a0e14"
_BG_RGB = (10, 14, 20)
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
_AV_SIZE = 220
_CANVAS_W = 360
_CANVAS_H = 360
_FPS = 24
_ANIM_FRAMES = 24  # Total pre-rendered motion frames

# Voice bars
_N_BARS = 29
_BAR_W = 3
_BAR_GAP = 1
_BAR_MAX_H = 16

# Glow
_GLOW = {"idle": _MUTED, "listening": _GREEN, "thinking": _AMBER, "speaking": _CYAN}
_GLOW_RGB = {
    "idle": (61, 68, 80), "listening": (0, 210, 106),
    "thinking": (255, 165, 2), "speaking": (0, 210, 255),
}

# Particles
_N_PARTICLES = 16


class _Particle:
    __slots__ = ("angle", "radius", "speed", "size")
    def __init__(self):
        self.angle = random.uniform(0, 2 * math.pi)
        self.radius = random.uniform(118, 148)
        self.speed = random.uniform(0.004, 0.012)
        self.size = random.uniform(1.0, 2.4)


def _make_circular(img: "Image.Image") -> "Image.Image":
    """Apply circular mask with anti-aliased edge."""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse([3, 3, w - 4, h - 4], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(1.5))
    img = img.copy()
    img.putalpha(mask)
    bg = Image.new("RGBA", (w, h), (*_BG_RGB, 255))
    bg.paste(img, (0, 0), img)
    return bg.convert("RGB")


def _prepare_all_frames():
    """Pre-render multiple animation frames with PIL transforms."""
    if not _HAS_PIL or not _AVATAR_PATH.exists():
        return [], [], []

    try:
        raw = Image.open(_AVATAR_PATH).convert("RGBA")
        # Work at slightly larger size for crop transforms
        work_size = _AV_SIZE + 20
        raw = raw.resize((work_size, work_size), Image.Resampling.LANCZOS)

        motion_frames = []  # 24 frames: head tilt + zoom + light
        blink_frames = []   # 3 blink stages
        speak_frames = []   # 4 speaking variations

        cx, cy = work_size // 2, work_size // 2
        half = _AV_SIZE // 2

        # ── Motion frames: continuous head/breathing cycle ────────────
        for i in range(_ANIM_FRAMES):
            t = i / _ANIM_FRAMES * 2 * math.pi

            # Head tilt: shift image left/right by up to 3px
            dx = int(math.sin(t) * 3)
            # Breathing: slight vertical shift
            dy = int(math.sin(t * 0.5) * 2)
            # Zoom: subtle 1-2% scale variation
            zoom = 1.0 + math.sin(t * 0.5) * 0.015

            # Light temperature: subtle warm/cool shift
            temp_shift = math.sin(t * 0.3) * 0.03  # ±3% brightness

            # Crop with offset for head tilt effect
            left = cx - int(half / zoom) + dx
            top = cy - int(half / zoom) + dy
            right = cx + int(half / zoom) + dx
            bottom = cy + int(half / zoom) + dy

            # Clamp crop bounds
            left = max(0, left)
            top = max(0, top)
            right = min(work_size, right)
            bottom = min(work_size, bottom)

            frame = raw.crop((left, top, right, bottom))
            frame = frame.resize((_AV_SIZE, _AV_SIZE), Image.Resampling.LANCZOS)

            # Apply brightness variation
            if abs(temp_shift) > 0.005:
                enhancer = ImageEnhance.Brightness(frame)
                frame = enhancer.enhance(1.0 + temp_shift)
                frame = frame.convert("RGBA")

            motion_frames.append(ImageTk.PhotoImage(_make_circular(frame)))

        # ── Blink frames ─────────────────────────────────────────────
        for alpha in [120, 200, 200]:  # closing, closed, opening
            bframe = raw.copy()
            # Crop to standard view
            left = cx - half
            top = cy - half
            bframe = bframe.crop((left, top, left + _AV_SIZE, top + _AV_SIZE))
            bframe = bframe.resize((_AV_SIZE, _AV_SIZE), Image.Resampling.LANCZOS)
            bd = ImageDraw.Draw(bframe)
            eye_y = _AV_SIZE // 2 - 12
            for side in [-1, 1]:
                ex = _AV_SIZE // 2 + side * 28
                bd.ellipse([ex - 16, eye_y - 3, ex + 16, eye_y + 5],
                           fill=(100, 70, 55, alpha))
            blink_frames.append(ImageTk.PhotoImage(_make_circular(bframe)))

        # ── Speaking frames ──────────────────────────────────────────
        for intensity in [0.0, 0.4, 0.8, 0.4]:
            sframe = raw.copy()
            left = cx - half
            top = cy - half
            sframe = sframe.crop((left, top, left + _AV_SIZE, top + _AV_SIZE))
            sframe = sframe.resize((_AV_SIZE, _AV_SIZE), Image.Resampling.LANCZOS)

            # Brighten slightly when speaking
            enhancer = ImageEnhance.Brightness(sframe)
            sframe = enhancer.enhance(1.0 + intensity * 0.08)
            sframe = sframe.convert("RGBA")

            # Cyan glow near mouth
            if intensity > 0:
                overlay = Image.new("RGBA", (_AV_SIZE, _AV_SIZE), (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)
                mouth_y = _AV_SIZE // 2 + 36
                glow_alpha = int(intensity * 35)
                od.ellipse([_AV_SIZE // 2 - 18, mouth_y - 6,
                            _AV_SIZE // 2 + 18, mouth_y + 6],
                           fill=(0, 200, 255, glow_alpha))
                sframe = Image.alpha_composite(sframe, overlay)

            speak_frames.append(ImageTk.PhotoImage(_make_circular(sframe)))

        logger.info(
            "Avatar frames: %d motion, %d blink, %d speak",
            len(motion_frames), len(blink_frames), len(speak_frames)
        )
        return motion_frames, blink_frames, speak_frames

    except Exception:
        logger.exception("Avatar frame prep failed")
        return [], [], []


class OverwatchGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PMC Overwatch")
        self.geometry("860x880")
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
        self._frame_idx = 0
        self._anim_id: Optional[str] = None
        self._pulse_id: Optional[str] = None
        self._pulse_vis = True
        self._dot_color = _MUTED

        # Blink state
        self._blink_cd = random.uniform(3.0, 5.0)
        self._blink_timer = 0.0
        self._blink_frame = -1  # -1 = not blinking

        # Speaking state
        self._speak_frame = 0
        self._speak_timer = 0.0

        # Voice bars
        self._bar_target = [0.0] * _N_BARS
        self._bar_current = [0.0] * _N_BARS

        # Particles
        self._particles = [_Particle() for _ in range(_N_PARTICLES)]

        # Pre-render all frames
        self._motion_frames, self._blink_frames, self._speak_frames = _prepare_all_frames()

        self._build_header()
        self._build_agent()
        self._build_log()
        self._build_footer()
        self._start_anim()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ══════════════════════════════════════════════════════════════════
    #  HEADER
    # ══════════════════════════════════════════════════════════════════
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

    # ══════════════════════════════════════════════════════════════════
    #  AGENT CANVAS
    # ══════════════════════════════════════════════════════════════════
    def _build_agent(self) -> None:
        self._cv = tk.Canvas(self, width=_CANVAS_W, height=_CANVAS_H,
                             bg=_BG, highlightthickness=0, bd=0)
        self._cv.pack(pady=(4, 0))
        self._av_status = ctk.CTkLabel(
            self, text="OFFLINE",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=_MUTED)
        self._av_status.pack(pady=(0, 4))

    def _render(self) -> None:
        cv = self._cv
        cv.delete("all")
        cx = _CANVAS_W // 2
        cy = _CANVAS_H // 2 - 10
        dt = 1.0 / _FPS
        glow_c = _GLOW.get(self._mode, _MUTED)
        glow_rgb = _GLOW_RGB.get(self._mode, (61, 68, 80))

        # ── Aura rings ────────────────────────────────────────────────
        if self._mode != "idle":
            pulse = (math.sin(self._phase * 0.6) + 1) * 0.5
            for i in range(3):
                r = _AV_SIZE // 2 + 16 + i * 14 + pulse * 6
                alpha = max(0, 50 - i * 18)
                rc = int(glow_rgb[0] * alpha / 255)
                gc = int(glow_rgb[1] * alpha / 255)
                bc = int(glow_rgb[2] * alpha / 255)
                c = f"#{max(10, rc):02x}{max(14, gc):02x}{max(20, bc):02x}"
                cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=c, width=2)

        # ── Particles ─────────────────────────────────────────────────
        for p in self._particles:
            p.angle += p.speed
            r = p.radius + math.sin(self._phase * 0.4 + p.angle * 2) * 6
            px = cx + math.cos(p.angle) * r
            py = cy + math.sin(p.angle) * r * 0.85
            sz = p.size
            if self._mode == "speaking":
                sz *= 1.3 + math.sin(self._phase * 3 + p.angle) * 0.4
            cv.create_oval(px - sz, py - sz, px + sz, py + sz,
                           fill=glow_c, outline="")

        # ── Select avatar frame ──────────────────────────────────────
        photo = None

        # Blink logic
        self._blink_timer += dt
        if self._blink_frame >= 0:
            # Currently blinking
            if self._blink_frame < len(self._blink_frames):
                photo = self._blink_frames[self._blink_frame]
            self._blink_timer += dt
            if self._blink_timer > 0.06:  # 60ms per blink frame
                self._blink_frame += 1
                self._blink_timer = 0
            if self._blink_frame >= len(self._blink_frames):
                self._blink_frame = -1
                self._blink_cd = random.uniform(3.0, 6.0)
        elif self._blink_timer > self._blink_cd:
            self._blink_frame = 0
            self._blink_timer = 0

        # Speaking frames (if speaking and not blinking)
        if self._mode == "speaking" and self._blink_frame < 0 and self._speak_frames:
            self._speak_timer += dt
            if self._speak_timer > 0.12:  # Swap every ~120ms
                self._speak_frame = (self._speak_frame + 1) % len(self._speak_frames)
                self._speak_timer = 0
            photo = self._speak_frames[self._speak_frame]

        # Default: motion frame (continuous head movement cycle)
        if photo is None and self._motion_frames:
            self._frame_idx = int(self._phase * 2.0) % len(self._motion_frames)
            photo = self._motion_frames[self._frame_idx]

        # Draw avatar
        if photo:
            cv.create_image(cx, cy, image=photo, anchor="center")
        else:
            r = _AV_SIZE // 2
            cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                           fill=_SURFACE, outline=glow_c, width=2)
            cv.create_text(cx, cy, text="SCAV-E",
                           font=("Segoe UI", 18, "bold"), fill=_TEXT)

        # ── Glow ring ─────────────────────────────────────────────────
        pulse = (math.sin(self._phase * 0.8) + 1) * 0.5
        ring_r = _AV_SIZE // 2 + 4 + pulse * 3
        ring_w = 3 if self._mode != "idle" else 1
        cv.create_oval(cx - ring_r, cy - ring_r,
                       cx + ring_r, cy + ring_r,
                       outline=glow_c, width=ring_w)

        # ── Speaking: sound wave ripples ──────────────────────────────
        if self._mode == "speaking":
            for wi in range(3):
                wp = self._phase * 2.5 + wi * 2.0
                wr = _AV_SIZE // 2 + 10 + (wp % 4.0) * 12
                wa = max(0, 1.0 - (wp % 4.0) / 4.0)
                if wa > 0.05:
                    rv = int(glow_rgb[0] * wa * 0.6)
                    gv = int(glow_rgb[1] * wa * 0.6)
                    bv = int(glow_rgb[2] * wa * 0.6)
                    wc = f"#{max(10, rv):02x}{max(14, gv):02x}{max(20, bv):02x}"
                    cv.create_oval(cx - wr, cy - wr, cx + wr, cy + wr,
                                   outline=wc, width=1)

        # ── Holographic scan line ─────────────────────────────────────
        if self._mode != "idle":
            scan_y = cy - _AV_SIZE // 2 + int((self._phase * 40) % _AV_SIZE)
            cv.create_line(cx - _AV_SIZE // 2, scan_y,
                           cx + _AV_SIZE // 2, scan_y,
                           fill=glow_c, width=1, stipple="gray25")

        # ── Voice bars ────────────────────────────────────────────────
        total_w = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        bx = cx - total_w // 2
        by = cy + _AV_SIZE // 2 + 22
        for i in range(_N_BARS):
            h = max(1, int(self._bar_current[i] * _BAR_MAX_H))
            x = bx + i * (_BAR_W + _BAR_GAP)
            cv.create_rectangle(x, by - h, x + _BAR_W, by, fill=glow_c, outline="")
            cv.create_rectangle(x, by, x + _BAR_W, by + h, fill=glow_c, outline="")

    # ── Animation loop ────────────────────────────────────────────────
    def _start_anim(self) -> None:
        self._tick()

    def _tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        self._phase += 0.1

        # Bar targets
        if self._mode == "speaking":
            for i in range(_N_BARS):
                cw = 1.0 - abs(i - _N_BARS // 2) / (_N_BARS // 2) * 0.4
                self._bar_target[i] = max(0.1, random.uniform(0.2, 1.0) * cw)
        elif self._mode == "thinking":
            for i in range(_N_BARS):
                w = (math.sin(self._phase * 3.0 + i * 0.3) + 1) * 0.5
                self._bar_target[i] = w * 0.18
        elif self._mode == "listening":
            for i in range(_N_BARS):
                self._bar_target[i] = 0.02 + (math.sin(self._phase * 0.6 + i * 0.15) + 1) * 0.02
        else:
            for i in range(_N_BARS):
                self._bar_target[i] = 0.0

        for i in range(_N_BARS):
            self._bar_current[i] += (self._bar_target[i] - self._bar_current[i]) * 0.35

        self._render()
        self._anim_id = self.after(1000 // _FPS, self._tick)

    # ══════════════════════════════════════════════════════════════════
    #  LOG
    # ══════════════════════════════════════════════════════════════════
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

    # ══════════════════════════════════════════════════════════════════
    #  FOOTER
    # ══════════════════════════════════════════════════════════════════
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
        ctk.CTkLabel(inner, text="v10.0", font=ctk.CTkFont(size=11),
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
