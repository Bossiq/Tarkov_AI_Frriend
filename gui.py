"""
PMC Overwatch GUI — Sprite-based animated avatar with real facial expressions.

Design (VTuber / Visual Novel style):
  * 6 expression sprites: neutral, talk_a, talk_b, blink, think + original
  * Speaking: cycles through mouth-open sprites (like real lip-sync)
  * Blinking: swaps to blink sprite every 3-6s for ~180ms
  * Thinking: shows thinking expression sprite
  * Listening: subtle idle animation with neutral expression
  * Head micro-motion via PIL crop offset (±2px, continuous)
"""

import logging
import math
import platform
import random
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

# ── Asset paths ──────────────────────────────────────────────────────
_ASSETS = Path(__file__).parent / "assets"
_SPRITE_FILES = {
    "neutral": _ASSETS / "neutral.png",
    "talk_a": _ASSETS / "talk_a.png",
    "talk_b": _ASSETS / "talk_b.png",
    "blink": _ASSETS / "blink.png",
    "think": _ASSETS / "think.png",
    "avatar": _ASSETS / "avatar.png",  # fallback / listen
}

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
_AV_SIZE = 240
_CANVAS_W = 380
_CANVAS_H = 380
_FPS = 24
_MOTION_STEPS = 32  # frames per head-motion cycle

# Voice bars
_N_BARS = 29
_BAR_W = 3
_BAR_GAP = 1
_BAR_MAX_H = 16

# Glow per state
_GLOW = {"idle": _MUTED, "listening": _GREEN, "thinking": _AMBER, "speaking": _CYAN}
_GLOW_RGB = {
    "idle": (61, 68, 80), "listening": (0, 210, 106),
    "thinking": (255, 165, 2), "speaking": (0, 210, 255),
}

# Particles
_N_PARTICLES = 14


class _Particle:
    __slots__ = ("angle", "radius", "speed", "size")
    def __init__(self):
        self.angle = random.uniform(0, 2 * math.pi)
        self.radius = random.uniform(126, 158)
        self.speed = random.uniform(0.003, 0.010)
        self.size = random.uniform(1.0, 2.2)


def _load_sprite(path: Path, size: int) -> Optional["Image.Image"]:
    """Load a sprite, resize, and return as RGBA PIL image."""
    if not _HAS_PIL or not path.exists():
        return None
    try:
        img = Image.open(path).convert("RGBA")
        img = img.resize((size + 16, size + 16), Image.Resampling.LANCZOS)
        return img
    except Exception:
        logger.exception("Failed to load sprite: %s", path.name)
        return None


def _apply_circular_mask(img: "Image.Image", size: int) -> "Image.Image":
    """Crop center, apply circular mask, composite on dark bg."""
    # Crop center region
    w, h = img.size
    left = (w - size) // 2
    top = (h - size) // 2
    cropped = img.crop((left, top, left + size, top + size))

    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse([3, 3, size - 4, size - 4], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(1.5))
    cropped.putalpha(mask)

    bg = Image.new("RGBA", (size, size), (*_BG_RGB, 255))
    bg.paste(cropped, (0, 0), cropped)
    return bg.convert("RGB")


def _prepare_sprites():
    """Load all expression sprites and create motion-shifted variants."""
    sprites: Dict[str, "Image.Image"] = {}
    for name, path in _SPRITE_FILES.items():
        img = _load_sprite(path, _AV_SIZE)
        if img:
            sprites[name] = img
            logger.info("Loaded sprite: %s (%s)", name, path.name)

    if not sprites:
        return {}, []

    # Create motion-shifted frames from 'neutral' or 'avatar' base
    base = sprites.get("neutral", sprites.get("avatar"))
    motion_frames: List["ImageTk.PhotoImage"] = []
    if base:
        for i in range(_MOTION_STEPS):
            t = i / _MOTION_STEPS * 2 * math.pi
            dx = int(math.sin(t) * 2)  # ±2px head tilt
            dy = int(math.sin(t * 0.5) * 1.5)  # ±1.5px breathing

            w, h = base.size
            cx, cy = w // 2, h // 2
            half = _AV_SIZE // 2

            left = max(0, cx - half + dx)
            top = max(0, cy - half + dy)
            right = min(w, left + _AV_SIZE)
            bottom = min(h, top + _AV_SIZE)

            frame = base.crop((left, top, right, bottom))
            if frame.size != (_AV_SIZE, _AV_SIZE):
                frame = frame.resize((_AV_SIZE, _AV_SIZE), Image.Resampling.LANCZOS)

            motion_frames.append(
                ImageTk.PhotoImage(_apply_circular_mask(
                    frame.resize((_AV_SIZE + 16, _AV_SIZE + 16), Image.Resampling.LANCZOS),
                    _AV_SIZE
                ))
            )

    # Pre-render expression PhotoImages
    expr_photos: Dict[str, "ImageTk.PhotoImage"] = {}
    for name, img in sprites.items():
        expr_photos[name] = ImageTk.PhotoImage(_apply_circular_mask(img, _AV_SIZE))

    logger.info(
        "Sprites ready: %d expressions, %d motion frames",
        len(expr_photos), len(motion_frames)
    )
    return expr_photos, motion_frames


class OverwatchGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PMC Overwatch")
        self.geometry("860x900")
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

        # Blink timer
        self._blink_cd = random.uniform(3.0, 5.5)
        self._blink_timer = 0.0
        self._blink_active = False
        self._blink_duration = 0.0

        # Speaking lip-sync
        self._talk_timer = 0.0
        self._talk_frame = 0  # 0=neutral, 1=talk_a, 2=talk_b

        # Voice bars
        self._bar_target = [0.0] * _N_BARS
        self._bar_current = [0.0] * _N_BARS

        # Particles
        self._particles = [_Particle() for _ in range(_N_PARTICLES)]

        # Load sprites
        self._expr_photos, self._motion_frames = _prepare_sprites()

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
            p = (math.sin(self._phase * 0.6) + 1) * 0.5
            for i in range(3):
                r = _AV_SIZE // 2 + 14 + i * 12 + p * 5
                a = max(0, 45 - i * 16)
                rc = int(glow_rgb[0] * a / 255)
                gc = int(glow_rgb[1] * a / 255)
                bc = int(glow_rgb[2] * a / 255)
                c = f"#{max(10, rc):02x}{max(14, gc):02x}{max(20, bc):02x}"
                cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=c, width=2)

        # ── Particles ─────────────────────────────────────────────────
        for pt in self._particles:
            pt.angle += pt.speed
            r = pt.radius + math.sin(self._phase * 0.4 + pt.angle * 2) * 5
            px = cx + math.cos(pt.angle) * r
            py = cy + math.sin(pt.angle) * r * 0.85
            sz = pt.size
            if self._mode == "speaking":
                sz *= 1.2 + math.sin(self._phase * 3 + pt.angle) * 0.3
            cv.create_oval(px - sz, py - sz, px + sz, py + sz,
                           fill=glow_c, outline="")

        # ══════════════════════════════════════════════════════════════
        #  SPRITE SELECTION (real facial expressions)
        # ══════════════════════════════════════════════════════════════
        photo = None

        # --- Blink ---
        self._blink_timer += dt
        if self._blink_active:
            self._blink_duration += dt
            if self._blink_duration < 0.18:  # 180ms blink
                photo = self._expr_photos.get("blink")
            else:
                self._blink_active = False
                self._blink_duration = 0.0
                self._blink_timer = 0.0
                self._blink_cd = random.uniform(3.0, 6.0)
        elif self._blink_timer >= self._blink_cd:
            self._blink_active = True
            self._blink_duration = 0.0

        # --- Speaking: cycle through mouth sprites ---
        if self._mode == "speaking" and not self._blink_active:
            self._talk_timer += dt
            # Cycle: neutral → talk_a → talk_b → talk_a → neutral → ...
            # at ~8 swaps/sec for realistic lip movement
            if self._talk_timer > 0.12:
                self._talk_frame = (self._talk_frame + 1) % 4
                self._talk_timer = 0.0

            talk_seq = ["neutral", "talk_a", "talk_b", "talk_a"]
            sprite_name = talk_seq[self._talk_frame]
            photo = self._expr_photos.get(sprite_name)

        # --- Thinking: thinking expression ---
        elif self._mode == "thinking" and not self._blink_active:
            photo = self._expr_photos.get("think")

        # --- Listening/Idle: motion frames (subtle head movement) ---
        if photo is None and not self._blink_active:
            if self._motion_frames:
                idx = int(self._phase * 1.5) % len(self._motion_frames)
                # Use motion frame (continuous head micro-movement)
                cv.create_image(cx, cy, image=self._motion_frames[idx], anchor="center")
                photo = None  # Skip the photo draw below
            else:
                photo = self._expr_photos.get("neutral", self._expr_photos.get("avatar"))

        # Draw the expression sprite
        if photo is not None:
            cv.create_image(cx, cy, image=photo, anchor="center")

        # ── Glow border ring ──────────────────────────────────────────
        pulse = (math.sin(self._phase * 0.8) + 1) * 0.5
        ring_r = _AV_SIZE // 2 + 3 + pulse * 2
        ring_w = 3 if self._mode != "idle" else 1
        cv.create_oval(cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r,
                       outline=glow_c, width=ring_w)

        # ── Speaking: ripple waves ────────────────────────────────────
        if self._mode == "speaking":
            for wi in range(3):
                wp = self._phase * 2.5 + wi * 2.0
                wr = _AV_SIZE // 2 + 8 + (wp % 4.0) * 10
                wa = max(0, 1.0 - (wp % 4.0) / 4.0)
                if wa > 0.05:
                    rv = int(glow_rgb[0] * wa * 0.5)
                    gv = int(glow_rgb[1] * wa * 0.5)
                    bv = int(glow_rgb[2] * wa * 0.5)
                    wc = f"#{max(10, rv):02x}{max(14, gv):02x}{max(20, bv):02x}"
                    cv.create_oval(cx - wr, cy - wr, cx + wr, cy + wr,
                                   outline=wc, width=1)

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

        # Update bar targets
        if self._mode == "speaking":
            for i in range(_N_BARS):
                cw = 1.0 - abs(i - _N_BARS // 2) / (_N_BARS // 2) * 0.4
                self._bar_target[i] = max(0.1, random.uniform(0.2, 1.0) * cw)
        elif self._mode == "thinking":
            for i in range(_N_BARS):
                w = (math.sin(self._phase * 3.0 + i * 0.3) + 1) * 0.5
                self._bar_target[i] = w * 0.15
        elif self._mode == "listening":
            for i in range(_N_BARS):
                self._bar_target[i] = 0.02 + (math.sin(self._phase * 0.5 + i * 0.15) + 1) * 0.02
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
        ctk.CTkLabel(inner, text="v11.0", font=ctk.CTkFont(size=11),
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
