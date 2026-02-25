"""
PMC Overwatch GUI — stream-ready animated AI avatar.

Avatar animation uses frame-swapping between 3 pre-rendered expressions:
  • Idle: eyes open, gentle smile (avatar.png)
  • Speaking: mouth open, talking (avatar_speaking.png)
  • Blinking: eyes closed (avatar_blinking.png)
Swaps between frames based on state, with randomized blinking.
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
_FRAMES = {
    "idle": _ASSETS / "avatar.png",
    "speaking": _ASSETS / "avatar_speaking.png",
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
_AV_SIZE = 200
_CANVAS_SIZE = _AV_SIZE + 32
_FPS = 15


class OverwatchGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PMC Overwatch — Tarkov AI")
        self.geometry("760x680")
        self.minsize(560, 500)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=_BG)

        # State
        self._toggle_cb: Optional[Callable[[bool], None]] = None
        self._is_running = False
        self.shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []

        # Animation
        self._mode = "idle"
        self._phase = 0.0
        self._anim_id: Optional[str] = None
        self._pulse_id: Optional[str] = None
        self._pulse_vis = True
        self._dot_color = _MUTED

        # Blink timing
        self._blink_active = False
        self._blink_frames_left = 0
        self._next_blink_at = random.randint(40, 80)  # frames until next blink
        self._frame_counter = 0

        # Speaking mouth toggle (alternates between idle/speaking frames)
        self._speak_mouth_open = False
        self._speak_toggle_counter = 0

        # Avatar frames
        self._avatar_frames: dict[str, Optional[ImageTk.PhotoImage]] = {
            "idle": None, "speaking": None, "blinking": None,
        }
        self._load_frames()

        # Build
        self._build_header()
        self._build_avatar()
        self._build_log()
        self._build_footer()
        self._start_anim()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_frames(self) -> None:
        """Load and prepare all avatar expression frames."""
        if not _HAS_PIL:
            return
        for name, path in _FRAMES.items():
            if not path.exists():
                logger.warning("Avatar frame missing: %s", path)
                continue
            try:
                img = Image.open(path).convert("RGBA")
                img = img.resize((_AV_SIZE, _AV_SIZE), Image.LANCZOS)
                # Circular mask with soft edge
                mask = Image.new("L", (_AV_SIZE, _AV_SIZE), 0)
                d = ImageDraw.Draw(mask)
                d.ellipse((0, 0, _AV_SIZE - 1, _AV_SIZE - 1), fill=255)
                mask = mask.filter(ImageFilter.GaussianBlur(1))
                img.putalpha(mask)
                # Composite on dark bg
                bg = Image.new("RGBA", (_AV_SIZE, _AV_SIZE), (13, 17, 23, 255))
                bg.paste(img, (0, 0), img)
                self._avatar_frames[name] = ImageTk.PhotoImage(bg.convert("RGB"))
                logger.info("Loaded avatar frame: %s", name)
            except Exception:
                logger.exception("Failed to load frame: %s", name)

    # ══════════════════════════════════════════════════════════════════
    #  HEADER
    # ══════════════════════════════════════════════════════════════════
    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, corner_radius=12, fg_color=_CARD,
                           border_width=1, border_color=_BORDER)
        hdr.pack(fill="x", padx=16, pady=(14, 0))
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        logo = ctk.CTkFrame(inner, fg_color="transparent")
        logo.pack(side="left")
        ctk.CTkLabel(logo, text="⚔", font=ctk.CTkFont(size=28),
                     text_color=_GREEN).pack(side="left", padx=(0, 8))
        ttl = ctk.CTkFrame(logo, fg_color="transparent")
        ttl.pack(side="left")
        ctk.CTkLabel(ttl, text="PMC Overwatch",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=_TEXT).pack(anchor="w")
        ctk.CTkLabel(ttl, text="Tarkov AI Companion",
                     font=ctk.CTkFont(size=11), text_color=_TEXT2).pack(anchor="w")

        self._btn = ctk.CTkButton(
            inner, text="▶  Start Overwatch", width=180, height=42,
            corner_radius=10, font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=_GREEN, hover_color=_GREEN_H, text_color="white",
            command=self._on_toggle)
        self._btn.pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  AVATAR — frame-swapping animation
    # ══════════════════════════════════════════════════════════════════
    def _build_avatar(self) -> None:
        self._cv = tk.Canvas(
            self, width=_CANVAS_SIZE, height=_CANVAS_SIZE,
            bg=_BG, highlightthickness=0, bd=0,
        )
        self._cv.pack(pady=(10, 0))

        self._av_status = ctk.CTkLabel(
            self, text="Offline",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_MUTED,
        )
        self._av_status.pack(pady=(2, 4))

    def _get_current_frame(self) -> Optional[ImageTk.PhotoImage]:
        """Decide which frame to show based on state + blink/speak timing."""
        # Blink takes priority (brief overlay)
        if self._blink_active and self._avatar_frames.get("blinking"):
            return self._avatar_frames["blinking"]

        # Speaking: alternate between idle and speaking frames
        if self._mode == "speaking" and self._speak_mouth_open:
            return self._avatar_frames.get("speaking") or self._avatar_frames.get("idle")

        # Thinking: show speaking frame (slightly open mouth)
        if self._mode == "thinking":
            return self._avatar_frames.get("speaking") or self._avatar_frames.get("idle")

        return self._avatar_frames.get("idle")

    def _render_frame(self) -> None:
        cv = self._cv
        cv.delete("all")
        cx = _CANVAS_SIZE // 2
        # Gentle bob
        bob = math.sin(self._phase * 0.5) * 2.0
        cy = _CANVAS_SIZE // 2 + bob
        r = _AV_SIZE // 2

        # Glow ring
        if self._mode == "speaking":
            glow_color, glow_extra = _CYAN, 4 + math.sin(self._phase * 1.5) * 3
        elif self._mode == "thinking":
            glow_color, glow_extra = _AMBER, 2
        elif self._mode == "listening":
            glow_color, glow_extra = _GREEN, 2
        else:
            glow_color, glow_extra = _MUTED, 0

        glow_r = r + 10 + glow_extra
        pulse_w = 2 + int((math.sin(self._phase) + 1) * 1.5)
        cv.create_oval(cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
                       outline=glow_color, width=pulse_w)

        # Avatar image
        frame = self._get_current_frame()
        if frame is not None:
            cv.create_image(cx, cy, image=frame, anchor="center")
        else:
            cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                           fill=_SURFACE, outline=_BORDER, width=2)
            cv.create_text(cx, cy, text="🎮", font=("", 48))

    # ── Animation loop ────────────────────────────────────────────────
    def _start_anim(self) -> None:
        self._tick()

    def _tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        self._phase += 0.12
        self._frame_counter += 1

        # ── Blink logic ──────────────────────────────────────────────
        if self._blink_active:
            self._blink_frames_left -= 1
            if self._blink_frames_left <= 0:
                self._blink_active = False
                self._next_blink_at = self._frame_counter + random.randint(40, 90)
        elif self._frame_counter >= self._next_blink_at:
            self._blink_active = True
            self._blink_frames_left = 3  # Blink lasts 3 frames (~200ms)

        # ── Mouth toggle for speaking ─────────────────────────────────
        if self._mode == "speaking":
            self._speak_toggle_counter += 1
            if self._speak_toggle_counter >= random.randint(2, 5):
                self._speak_mouth_open = not self._speak_mouth_open
                self._speak_toggle_counter = 0
        else:
            self._speak_mouth_open = False
            self._speak_toggle_counter = 0

        self._render_frame()
        self._anim_id = self.after(1000 // _FPS, self._tick)

    # ══════════════════════════════════════════════════════════════════
    #  LOG
    # ══════════════════════════════════════════════════════════════════
    def _build_log(self) -> None:
        f = ctk.CTkFrame(self, corner_radius=12, fg_color=_CARD,
                         border_width=1, border_color=_BORDER)
        f.pack(fill="both", expand=True, padx=16, pady=8)
        ctk.CTkLabel(f, text="📋  Activity Log",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=_TEXT2).pack(anchor="w", padx=14, pady=(10, 0))
        self._log = ctk.CTkTextbox(
            f, font=ctk.CTkFont(family="Menlo", size=12),
            corner_radius=8, fg_color=_SURFACE,
            text_color=_TEXT, state="disabled", wrap="word", border_width=0)
        self._log.pack(fill="both", expand=True, padx=10, pady=(6, 10))

    # ══════════════════════════════════════════════════════════════════
    #  FOOTER
    # ══════════════════════════════════════════════════════════════════
    def _build_footer(self) -> None:
        ft = ctk.CTkFrame(self, corner_radius=12, fg_color=_CARD,
                          border_width=1, border_color=_BORDER, height=40)
        ft.pack(fill="x", padx=16, pady=(0, 14))
        ft.pack_propagate(False)
        inner = ctk.CTkFrame(ft, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=8)
        self._dot = ctk.CTkLabel(inner, text="●", font=ctk.CTkFont(size=12),
                                 text_color=_MUTED, width=16)
        self._dot.pack(side="left", padx=(0, 6))
        self._status_lbl = ctk.CTkLabel(inner, text="Offline",
                                        font=ctk.CTkFont(size=12), text_color=_TEXT2)
        self._status_lbl.pack(side="left")
        ctk.CTkLabel(inner, text="v3.1", font=ctk.CTkFont(size=11),
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
        self._btn.configure(text="▶  Start Overwatch", fg_color=_GREEN,
                            hover_color=_GREEN_H)
        self._do_status("Offline")

    def _on_toggle(self) -> None:
        self._is_running = not self._is_running
        if self._is_running:
            self._btn.configure(text="⏹  Stop Overwatch", fg_color=_RED,
                                hover_color=_RED_H)
            self._do_status("Starting…")
            self.log("Overwatch activated ✅")
        else:
            self._btn.configure(text="▶  Start Overwatch", fg_color=_GREEN,
                                hover_color=_GREEN_H)
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
