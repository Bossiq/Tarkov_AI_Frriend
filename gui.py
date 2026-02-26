"""
PMC Overwatch GUI — photorealistic PIL-rendered AI agent.

Design:
  * Pre-rendered avatar using PIL with skin-tone gradients,
    proper facial proportions, and realistic shading
  * Animated overlays: glow rings, particles, voice bars
  * Speaking mouth animation and reactive eyes
  * State-driven colour scheme for all effects
  * No external image files needed — self-contained
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

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

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
_PURPLE = "#a855f7"
_TEXT = "#e6edf3"
_TEXT2 = "#7b8794"
_MUTED = "#3d4450"
_BORDER = "#252b35"

# Skin tones
_SKIN = (180, 145, 120)
_SKIN_SHADOW = (145, 115, 95)
_SKIN_HIGHLIGHT = (210, 175, 150)
_HAIR = (35, 25, 20)
_LIP = (165, 95, 95)

# Agent sizing
_AV_SIZE = 200
_CANVAS_W = 340
_CANVAS_H = 380
_FPS = 20

# Voice bars
_N_BARS = 25
_BAR_W = 3
_BAR_GAP = 2
_BAR_MAX_H = 22

# Glow per state
_GLOW = {
    "idle": _MUTED,
    "listening": _GREEN,
    "thinking": _AMBER,
    "speaking": _CYAN,
}
_GLOW_RGB = {
    "idle": (61, 68, 80),
    "listening": (0, 210, 106),
    "thinking": (255, 165, 2),
    "speaking": (0, 210, 255),
}

# Particles
_N_PARTICLES = 18


class _Particle:
    __slots__ = ("angle", "radius", "speed", "size")

    def __init__(self):
        self.angle = random.uniform(0, 2 * math.pi)
        self.radius = random.uniform(105, 145)
        self.speed = random.uniform(0.003, 0.012)
        self.size = random.uniform(1.0, 2.5)


def _hex(rgb):
    """Convert RGB tuple to hex string."""
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _render_avatar() -> Optional["Image.Image"]:
    """Pre-render a photorealistic female face using PIL drawing."""
    if not _HAS_PIL:
        return None
    try:
        sz = _AV_SIZE
        img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        cx, cy = sz // 2, sz // 2 + 8

        # ── Neck ─────────────────────────────────────────────────────
        neck_w, neck_h = 28, 35
        d.rounded_rectangle(
            [cx - neck_w, cy + 42, cx + neck_w, cy + 42 + neck_h],
            radius=8, fill=_SKIN_SHADOW
        )

        # ── Shoulders (implied by jacket collar) ─────────────────────
        collar_pts = [
            (cx - neck_w - 10, cy + 55), (cx - 85, cy + sz // 2 + 5),
            (cx - 80, sz), (cx + 80, sz),
            (cx + 85, cy + sz // 2 + 5), (cx + neck_w + 10, cy + 55),
        ]
        d.polygon(collar_pts, fill=(30, 35, 40))
        # Collar V
        d.polygon(
            [(cx - 15, cy + 52), (cx, cy + 72), (cx + 15, cy + 52)],
            fill=(20, 25, 30)
        )

        # ── Face (oval) ──────────────────────────────────────────────
        face_w, face_h = 62, 72
        face_bb = [cx - face_w, cy - face_h, cx + face_w, cy + face_h - 20]
        d.ellipse(face_bb, fill=_SKIN)

        # Face shadow (sides + jaw)
        shadow_bb = [cx - face_w - 2, cy - face_h + 10,
                     cx - face_w + 18, cy + face_h - 25]
        d.ellipse(shadow_bb, fill=_SKIN_SHADOW)
        shadow_bb_r = [cx + face_w - 18, cy - face_h + 10,
                       cx + face_w + 2, cy + face_h - 25]
        d.ellipse(shadow_bb_r, fill=_SKIN_SHADOW)

        # Jaw shadow
        jaw_bb = [cx - face_w + 5, cy + 15, cx + face_w - 5, cy + face_h - 15]
        d.ellipse(jaw_bb, fill=_SKIN_SHADOW)
        # re-fill center face
        inner_bb = [cx - face_w + 12, cy - face_h + 12,
                    cx + face_w - 12, cy + face_h - 28]
        d.ellipse(inner_bb, fill=_SKIN)

        # ── Ears ─────────────────────────────────────────────────────
        for side in [-1, 1]:
            ear_x = cx + side * (face_w - 5)
            d.ellipse([ear_x - 8, cy - 12, ear_x + 8, cy + 12],
                      fill=_SKIN_SHADOW)
            d.ellipse([ear_x - 5, cy - 8, ear_x + 5, cy + 8],
                      fill=_SKIN)

        # ── Hair (dark, swept style) ─────────────────────────────────
        # Top hair mass
        hair_bb = [cx - face_w - 8, cy - face_h - 15,
                   cx + face_w + 8, cy - face_h + 40]
        d.ellipse(hair_bb, fill=_HAIR)
        # Side hair left (longer)
        d.ellipse([cx - face_w - 12, cy - face_h, cx - face_w + 20, cy + 15],
                  fill=_HAIR)
        # Side hair right
        d.ellipse([cx + face_w - 20, cy - face_h, cx + face_w + 12, cy + 5],
                  fill=_HAIR)
        # Bangs
        d.ellipse([cx - 35, cy - face_h - 5, cx + 15, cy - face_h + 30],
                  fill=_HAIR)

        # ── Eyebrows ─────────────────────────────────────────────────
        brow_y = cy - 28
        for side in [-1, 1]:
            bx = cx + side * 22
            pts = [(bx - 14, brow_y + 2), (bx - 5, brow_y - 3),
                   (bx + 10, brow_y), (bx + 10, brow_y + 3),
                   (bx - 5, brow_y), (bx - 14, brow_y + 4)]
            if side == -1:
                pts = [(2 * cx - p[0], p[1]) for p in pts]
            d.polygon(pts, fill=(60, 45, 35))

        # ── Eyes ─────────────────────────────────────────────────────
        eye_y = cy - 15
        for side in [-1, 1]:
            ex = cx + side * 22
            # Eye white
            d.ellipse([ex - 13, eye_y - 6, ex + 13, eye_y + 6],
                      fill=(235, 230, 225))
            # Iris
            d.ellipse([ex - 7, eye_y - 6, ex + 7, eye_y + 6],
                      fill=(85, 140, 120))
            # Pupil
            d.ellipse([ex - 3, eye_y - 3, ex + 3, eye_y + 3],
                      fill=(15, 15, 15))
            # Pupil highlight
            d.ellipse([ex - 5, eye_y - 4, ex - 2, eye_y - 1],
                      fill=(255, 255, 255))
            # Upper eyelid shadow
            d.arc([ex - 14, eye_y - 8, ex + 14, eye_y + 4],
                  start=200, end=340, fill=(120, 90, 75), width=2)
            # Lower lash line
            d.arc([ex - 12, eye_y - 2, ex + 12, eye_y + 8],
                  start=20, end=160, fill=(80, 60, 50), width=1)

        # ── Nose ─────────────────────────────────────────────────────
        nose_y = cy + 2
        d.line([(cx, cy - 8), (cx - 3, nose_y + 8)],
               fill=_SKIN_SHADOW, width=1)
        # Nostrils
        d.ellipse([cx - 8, nose_y + 4, cx - 2, nose_y + 10],
                  fill=_SKIN_SHADOW)
        d.ellipse([cx + 2, nose_y + 4, cx + 8, nose_y + 10],
                  fill=_SKIN_SHADOW)

        # ── Mouth (closed, subtle smile) ─────────────────────────────
        mouth_y = cy + 22
        # Upper lip
        d.polygon([
            (cx - 16, mouth_y), (cx - 5, mouth_y - 3),
            (cx, mouth_y - 1), (cx + 5, mouth_y - 3),
            (cx + 16, mouth_y),
        ], fill=_LIP)
        # Lower lip
        d.ellipse([cx - 14, mouth_y - 1, cx + 14, mouth_y + 7], fill=_LIP)
        # Lip line
        d.line([(cx - 15, mouth_y), (cx + 15, mouth_y)],
               fill=(130, 75, 75), width=1)

        # ── Chin highlight ───────────────────────────────────────────
        d.ellipse([cx - 12, cy + 34, cx + 12, cy + 44],
                  fill=_SKIN_HIGHLIGHT)

        # ── Subtle blush ─────────────────────────────────────────────
        blush = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        bd = ImageDraw.Draw(blush)
        for side in [-1, 1]:
            bx = cx + side * 32
            bd.ellipse([bx - 15, cy + 2, bx + 15, cy + 18],
                       fill=(200, 140, 130, 40))
        img = Image.alpha_composite(img, blush)

        # ── Headset ──────────────────────────────────────────────────
        # Headband (top arc)
        d_final = ImageDraw.Draw(img)
        d_final.arc([cx - face_w - 15, cy - face_h - 20,
                     cx + face_w + 15, cy - face_h + 50],
                    start=200, end=340, fill=(50, 55, 60), width=4)
        # Left ear cup
        d_final.ellipse([cx - face_w - 18, cy - 18, cx - face_w + 2, cy + 12],
                        fill=(40, 45, 50), outline=(60, 65, 70), width=1)
        # Mic arm
        d_final.line([(cx - face_w - 5, cy + 5), (cx - 25, cy + 30)],
                     fill=(55, 60, 65), width=2)
        # Mic tip
        d_final.ellipse([cx - 28, cy + 27, cx - 20, cy + 35],
                        fill=(0, 210, 255), outline=(0, 180, 220))

        # Apply a very subtle blur for realism
        img = img.filter(ImageFilter.GaussianBlur(0.5))

        # Circular mask
        mask = Image.new("L", (sz, sz), 0)
        md = ImageDraw.Draw(mask)
        md.ellipse([0, 0, sz - 1, sz - 1], fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(1))
        img.putalpha(mask)

        # Composite on background
        bg = Image.new("RGBA", (sz, sz), (*_BG_RGB, 255))
        bg.paste(img, (0, 0), img)

        logger.info("PIL avatar rendered (%dpx)", sz)
        return bg.convert("RGB")
    except Exception:
        logger.exception("Avatar render failed")
        return None


class OverwatchGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PMC Overwatch")
        self.geometry("820x860")
        self.minsize(620, 640)
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

        # Pre-render avatar
        self._avatar_photo = None
        av_img = _render_avatar()
        if av_img:
            self._avatar_photo = ImageTk.PhotoImage(av_img)

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
        hdr.pack(fill="x", padx=20, pady=(18, 0))
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=14)

        logo = ctk.CTkFrame(inner, fg_color="transparent")
        logo.pack(side="left")
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
    #  AGENT — PIL-rendered avatar with animated overlays
    # ══════════════════════════════════════════════════════════════════
    def _build_agent(self) -> None:
        self._cv = tk.Canvas(self, width=_CANVAS_W, height=_CANVAS_H,
                             bg=_BG, highlightthickness=0, bd=0)
        self._cv.pack(pady=(10, 0))

        self._av_status = ctk.CTkLabel(
            self, text="Offline",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=_MUTED)
        self._av_status.pack(pady=(2, 6))

    def _render(self) -> None:
        cv = self._cv
        cv.delete("all")
        cx = _CANVAS_W // 2
        cy = _CANVAS_H // 2 - 30

        # Breathing bob
        bob = math.sin(self._phase * 0.3) * 2.5
        sway = math.sin(self._phase * 0.15) * 1.5
        cy_f = cy + bob
        glow_c = _GLOW.get(self._mode, _MUTED)

        # ── Particles ─────────────────────────────────────────────────
        for p in self._particles:
            p.angle += p.speed
            r = p.radius + math.sin(self._phase * 0.5 + p.angle * 2) * 6
            px = cx + sway + math.cos(p.angle) * r
            py = cy_f + math.sin(p.angle) * r * 0.85
            sz = p.size
            if self._mode == "speaking":
                sz *= 1.0 + (math.sin(self._phase * 2.5 + p.angle) + 1) * 0.4
            cv.create_oval(px - sz, py - sz, px + sz, py + sz,
                           fill=glow_c, outline="")

        # ── Glow ring ─────────────────────────────────────────────────
        pulse = (math.sin(self._phase * 1.0) + 1) * 0.5
        ring_r = _AV_SIZE // 2 + 10 + pulse * 4
        ring_w = 3 if self._mode != "idle" else 1
        cv.create_oval(cx + sway - ring_r, cy_f - ring_r,
                       cx + sway + ring_r, cy_f + ring_r,
                       outline=glow_c, width=ring_w)

        # ── Avatar image ──────────────────────────────────────────────
        if self._avatar_photo:
            cv.create_image(cx + sway, cy_f, image=self._avatar_photo,
                            anchor="center")
        else:
            # Fallback: simple circle
            r = _AV_SIZE // 2
            cv.create_oval(cx - r, cy_f - r, cx + r, cy_f + r,
                           fill=_SURFACE, outline=_BORDER, width=2)
            cv.create_text(cx, cy_f, text="SCAV-E", font=("", 20),
                           fill=_TEXT)

        # ── Mic glow (when speaking) ──────────────────────────────────
        if self._mode == "speaking":
            mic_x = cx + sway - 24
            mic_y = cy_f + 23
            glow_sz = 6 + math.sin(self._phase * 4) * 3
            cv.create_oval(mic_x - glow_sz, mic_y - glow_sz,
                           mic_x + glow_sz, mic_y + glow_sz,
                           fill=_CYAN, outline="")

        # ── Voice bars ────────────────────────────────────────────────
        total_w = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        bx_start = cx - total_w // 2
        by_base = cy_f + _AV_SIZE // 2 + 24

        for i in range(_N_BARS):
            h = max(1, int(self._bar_current[i] * _BAR_MAX_H))
            x = bx_start + i * (_BAR_W + _BAR_GAP)
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

        # Update bar targets
        if self._mode == "speaking":
            for i in range(_N_BARS):
                center_w = 1.0 - abs(i - _N_BARS // 2) / (_N_BARS // 2) * 0.4
                self._bar_target[i] = max(0.1, random.uniform(0.15, 1.0) * center_w)
        elif self._mode == "thinking":
            for i in range(_N_BARS):
                wave = (math.sin(self._phase * 3.0 + i * 0.4) + 1) * 0.5
                self._bar_target[i] = wave * 0.25
        elif self._mode == "listening":
            for i in range(_N_BARS):
                self._bar_target[i] = 0.03 + (math.sin(self._phase * 0.5 + i * 0.2) + 1) * 0.03
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
        self._dot = ctk.CTkLabel(inner, text="*", font=ctk.CTkFont(size=14),
                                 text_color=_MUTED, width=16)
        self._dot.pack(side="left", padx=(0, 6))
        self._status_lbl = ctk.CTkLabel(inner, text="Offline",
                                        font=ctk.CTkFont(size=12), text_color=_TEXT2)
        self._status_lbl.pack(side="left")
        ctk.CTkLabel(inner, text="v7.0", font=ctk.CTkFont(size=11),
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
