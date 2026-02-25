"""
PMC Overwatch GUI — premium animated AI avatar with 6 expression frames.

Frame-swapping animation for realistic avatar behaviour:
  • idle:           neutral expression (avatar.png)
  • listening:      curious head tilt  (avatar_listening.png)
  • thinking:       contemplative look (avatar_thinking.png)
  • speaking:       mouth slightly open (avatar_speaking.png)
  • speaking_wide:  mouth wide open    (avatar_speaking_wide.png)
  • blinking:       eyes closed        (avatar_blinking.png)

Blinking triggers randomly (2-5s intervals) across ALL states.
Speaking alternates between 3 mouth frames for natural movement.
Glow ring + breathing bob for liveliness.
"""

import logging
import math
import random
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

# ── Paths ────────────────────────────────────────────────────────────
_ASSETS = Path(__file__).parent / "assets"
_ALL_FRAMES = {
    "idle": _ASSETS / "avatar.png",
    "listening": _ASSETS / "avatar_listening.png",
    "thinking": _ASSETS / "avatar_thinking.png",
    "speaking": _ASSETS / "avatar_speaking.png",
    "speaking_wide": _ASSETS / "avatar_speaking_wide.png",
    "blinking": _ASSETS / "avatar_blinking.png",
}

# ── Palette ──────────────────────────────────────────────────────────
_BG = "#0d1117"
_CARD = "#161b22"
_SURFACE = "#1c2128"
_GREEN = "#2ea043"
_GREEN_H = "#238636"
_RED = "#da3633"
_RED_H = "#b62324"
_AMBER = "#d29922"
_CYAN = "#58a6ff"
_TEXT = "#e6edf3"
_TEXT2 = "#8b949e"
_MUTED = "#484f58"
_BORDER = "#30363d"

# Avatar
_AV_SIZE = 220
_CANVAS_SIZE = _AV_SIZE + 36
_FPS = 15

# Ring glow per state
_GLOW = {
    "idle": _MUTED,
    "listening": _GREEN,
    "thinking": _AMBER,
    "speaking": _CYAN,
}


class OverwatchGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PMC Overwatch — Tarkov AI")
        self.geometry("780x720")
        self.minsize(580, 540)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=_BG)

        # State
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
        self._frame_counter = 0

        # Blink state
        self._blink_active = False
        self._blink_remaining = 0
        self._next_blink = random.randint(30, 75)

        # Mouth animation
        self._mouth_state = 0  # 0=closed, 1=open, 2=wide
        self._mouth_timer = 0

        # Load avatar frames
        self._frames: dict[str, Optional[ImageTk.PhotoImage]] = {}
        self._load_all_frames()

        # Build UI
        self._build_header()
        self._build_avatar()
        self._build_log()
        self._build_footer()
        self._start_anim()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Load all 6 expression frames ──────────────────────────────────
    def _load_all_frames(self) -> None:
        if not _HAS_PIL:
            return
        for name, path in _ALL_FRAMES.items():
            if not path.exists():
                logger.info("Frame not found: %s", path.name)
                continue
            try:
                img = Image.open(path).convert("RGBA")
                img = img.resize((_AV_SIZE, _AV_SIZE), Image.LANCZOS)
                # Soft circular mask
                mask = Image.new("L", (_AV_SIZE, _AV_SIZE), 0)
                d = ImageDraw.Draw(mask)
                d.ellipse((0, 0, _AV_SIZE - 1, _AV_SIZE - 1), fill=255)
                mask = mask.filter(ImageFilter.GaussianBlur(1))
                img.putalpha(mask)
                # Composite on app bg
                bg = Image.new("RGBA", (_AV_SIZE, _AV_SIZE), (13, 17, 23, 255))
                bg.paste(img, (0, 0), img)
                self._frames[name] = ImageTk.PhotoImage(bg.convert("RGB"))
            except Exception:
                logger.exception("Frame load error: %s", name)
        logger.info("Loaded %d avatar frames: %s", len(self._frames), list(self._frames))

    # ══════════════════════════════════════════════════════════════════
    #  HEADER
    # ══════════════════════════════════════════════════════════════════
    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, corner_radius=14, fg_color=_CARD,
                           border_width=1, border_color=_BORDER)
        hdr.pack(fill="x", padx=18, pady=(16, 0))
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=14)

        logo = ctk.CTkFrame(inner, fg_color="transparent")
        logo.pack(side="left")
        ctk.CTkLabel(logo, text="⚔", font=ctk.CTkFont(size=30),
                     text_color=_GREEN).pack(side="left", padx=(0, 10))
        ttl = ctk.CTkFrame(logo, fg_color="transparent")
        ttl.pack(side="left")
        ctk.CTkLabel(ttl, text="PMC Overwatch",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=_TEXT).pack(anchor="w")
        ctk.CTkLabel(ttl, text="AI Companion • Escape from Tarkov",
                     font=ctk.CTkFont(size=11), text_color=_TEXT2).pack(anchor="w")

        self._btn = ctk.CTkButton(
            inner, text="▶  Start", width=150, height=44,
            corner_radius=12, font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=_GREEN, hover_color=_GREEN_H, text_color="white",
            command=self._on_toggle)
        self._btn.pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  AVATAR — 6-frame animation
    # ══════════════════════════════════════════════════════════════════
    def _build_avatar(self) -> None:
        self._cv = tk.Canvas(self, width=_CANVAS_SIZE, height=_CANVAS_SIZE,
                             bg=_BG, highlightthickness=0, bd=0)
        self._cv.pack(pady=(12, 0))

        self._av_status = ctk.CTkLabel(
            self, text="Offline",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=_MUTED)
        self._av_status.pack(pady=(4, 6))

    def _pick_frame(self) -> Optional[ImageTk.PhotoImage]:
        """Pick the right expression frame based on state + blink + mouth."""
        # Blink overrides everything (brief)
        if self._blink_active and "blinking" in self._frames:
            return self._frames["blinking"]

        if self._mode == "speaking":
            if self._mouth_state == 2 and "speaking_wide" in self._frames:
                return self._frames["speaking_wide"]
            elif self._mouth_state == 1 and "speaking" in self._frames:
                return self._frames["speaking"]
            return self._frames.get("idle")

        if self._mode == "thinking":
            return self._frames.get("thinking") or self._frames.get("idle")

        if self._mode == "listening":
            return self._frames.get("listening") or self._frames.get("idle")

        return self._frames.get("idle")

    def _render(self) -> None:
        cv = self._cv
        cv.delete("all")
        cx = _CANVAS_SIZE // 2
        bob = math.sin(self._phase * 0.4) * 2.5
        cy = _CANVAS_SIZE // 2 + bob
        r = _AV_SIZE // 2

        # Glow ring
        glow_c = _GLOW.get(self._mode, _MUTED)
        pulse = (math.sin(self._phase * 1.2) + 1) * 0.5
        glow_r = r + 12 + pulse * 4 if self._mode == "speaking" else r + 10
        ring_w = 3 + int(pulse * 2) if self._mode != "idle" else 2
        cv.create_oval(cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
                       outline=glow_c, width=ring_w)

        # Second subtle outer ring for premium feel
        if self._mode != "idle":
            outer_r = glow_r + 6
            cv.create_oval(cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                           outline=glow_c, width=1)

        # Avatar frame
        frame = self._pick_frame()
        if frame is not None:
            cv.create_image(cx, cy, image=frame, anchor="center")
        else:
            cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                           fill=_SURFACE, outline=_BORDER, width=2)
            cv.create_text(cx, cy, text="🎮", font=("", 48))

    # ── Animation loop (15fps) ────────────────────────────────────────
    def _start_anim(self) -> None:
        self._tick()

    def _tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        self._phase += 0.1
        self._frame_counter += 1

        # ── Blink (across ALL states) ─────────────────────────────────
        if self._blink_active:
            self._blink_remaining -= 1
            if self._blink_remaining <= 0:
                self._blink_active = False
                self._next_blink = self._frame_counter + random.randint(30, 75)
        elif self._frame_counter >= self._next_blink:
            self._blink_active = True
            self._blink_remaining = 3  # ~200ms

        # ── Mouth cycle (speaking only) ───────────────────────────────
        if self._mode == "speaking":
            self._mouth_timer += 1
            cycle_len = random.randint(2, 4)
            if self._mouth_timer >= cycle_len:
                # Cycle: closed(0) → open(1) → wide(2) → open(1) → closed(0)
                self._mouth_state = random.choice([0, 1, 1, 2])
                self._mouth_timer = 0
        else:
            self._mouth_state = 0
            self._mouth_timer = 0

        self._render()
        self._anim_id = self.after(1000 // _FPS, self._tick)

    # ══════════════════════════════════════════════════════════════════
    #  LOG
    # ══════════════════════════════════════════════════════════════════
    def _build_log(self) -> None:
        f = ctk.CTkFrame(self, corner_radius=14, fg_color=_CARD,
                         border_width=1, border_color=_BORDER)
        f.pack(fill="both", expand=True, padx=18, pady=10)
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(hdr, text="📋  Activity Log",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=_TEXT2).pack(side="left")
        self._log = ctk.CTkTextbox(
            f, font=ctk.CTkFont(family="Menlo", size=12),
            corner_radius=10, fg_color=_SURFACE,
            text_color=_TEXT, state="disabled", wrap="word", border_width=0)
        self._log.pack(fill="both", expand=True, padx=12, pady=(6, 12))

    # ══════════════════════════════════════════════════════════════════
    #  FOOTER
    # ══════════════════════════════════════════════════════════════════
    def _build_footer(self) -> None:
        ft = ctk.CTkFrame(self, corner_radius=14, fg_color=_CARD,
                          border_width=1, border_color=_BORDER, height=42)
        ft.pack(fill="x", padx=18, pady=(0, 16))
        ft.pack_propagate(False)
        inner = ctk.CTkFrame(ft, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=10)
        self._dot = ctk.CTkLabel(inner, text="●", font=ctk.CTkFont(size=12),
                                 text_color=_MUTED, width=16)
        self._dot.pack(side="left", padx=(0, 6))
        self._status_lbl = ctk.CTkLabel(inner, text="Offline",
                                        font=ctk.CTkFont(size=12), text_color=_TEXT2)
        self._status_lbl.pack(side="left")
        ctk.CTkLabel(inner, text="v4.0", font=ctk.CTkFont(size=11),
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
            "listening": ("🎧  Listening…", _GREEN),
            "speaking": ("🎙  Speaking…", _CYAN),
            "thinking": ("🧠  Thinking…", _AMBER),
        }
        t, c = labels.get(mode, ("Offline", _MUTED))
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
        self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H)
        self._do_status("Offline")

    def _on_toggle(self) -> None:
        self._is_running = not self._is_running
        if self._is_running:
            self._btn.configure(text="⏹  Stop", fg_color=_RED, hover_color=_RED_H)
            self._do_status("Starting…")
            self.log("Overwatch activated ✅")
        else:
            self._btn.configure(text="▶  Start", fg_color=_GREEN, hover_color=_GREEN_H)
            self._do_status("Offline")
            self.log("Overwatch deactivated ⛔")
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
