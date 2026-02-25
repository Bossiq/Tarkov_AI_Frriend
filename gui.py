"""
PMC Overwatch GUI — premium dark-mode interface with animated AI avatar.

Features:
  • Large animated AI face: mouth opens/closes when speaking, eyes blink
  • Animated glow ring around avatar (color per state)
  • Minimal, stream-friendly dark UI
  • Thread-safe API for background updates
"""

import logging
import math
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)

# Try PIL for avatar
try:
    from PIL import Image, ImageDraw, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False
    logger.warning("Pillow not found — avatar will be text-only")

# ── Paths ────────────────────────────────────────────────────────────
_AVATAR_PATH = Path(__file__).parent / "assets" / "avatar.png"

# ── Colour palette ───────────────────────────────────────────────────
_BG_DARK = "#0d1117"
_BG_CARD = "#161b22"
_BG_SURFACE = "#1c2128"
_ACCENT_GREEN = "#2ea043"
_ACCENT_GREEN_HOVER = "#238636"
_ACCENT_RED = "#da3633"
_ACCENT_RED_HOVER = "#b62324"
_ACCENT_AMBER = "#d29922"
_ACCENT_CYAN = "#58a6ff"
_TEXT_PRIMARY = "#e6edf3"
_TEXT_SECONDARY = "#8b949e"
_TEXT_MUTED = "#484f58"
_BORDER = "#30363d"

# Avatar config
_AVATAR_SIZE = 200
_RING_COLORS = {
    "idle": "#30363d",
    "listening": "#2ea043",
    "speaking": "#58a6ff",
    "thinking": "#d29922",
}


class OverwatchGUI(ctk.CTk):
    """Main application window for PMC Overwatch."""

    def __init__(self) -> None:
        super().__init__()

        self.title("PMC Overwatch — Tarkov AI")
        self.geometry("820x760")
        self.minsize(600, 560)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=_BG_DARK)

        # State
        self._toggle_callback: Optional[Callable[[bool], None]] = None
        self._is_running = False
        self.shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []

        # Animation state
        self._status_color = _TEXT_MUTED
        self._pulse_job: Optional[str] = None
        self._pulse_visible = True
        self._vis_mode = "idle"
        self._anim_phase = 0.0
        self._anim_job: Optional[str] = None
        self._blink_state = False
        self._blink_timer = 0
        self._mouth_open = 0.0  # 0 = closed, 1 = fully open
        self._ring_color = _RING_COLORS["idle"]

        # Avatar images
        self._avatar_base = None    # PIL Image (no mouth overlay)
        self._avatar_photo = None   # Current displayed ImageTk.PhotoImage
        self._avatar_pil = None     # Base PIL RGBA image

        # Build UI
        self._build_header()
        self._build_avatar_section()
        self._build_log()
        self._build_footer()

        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ══════════════════════════════════════════════════════════════════
    #  HEADER
    # ══════════════════════════════════════════════════════════════════
    def _build_header(self) -> None:
        header = ctk.CTkFrame(
            self, corner_radius=12, fg_color=_BG_CARD,
            border_width=1, border_color=_BORDER,
        )
        header.pack(fill="x", padx=16, pady=(14, 0))

        inner = ctk.CTkFrame(header, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        logo = ctk.CTkFrame(inner, fg_color="transparent")
        logo.pack(side="left")
        ctk.CTkLabel(
            logo, text="⚔", font=ctk.CTkFont(size=28), text_color=_ACCENT_GREEN,
        ).pack(side="left", padx=(0, 8))

        titles = ctk.CTkFrame(logo, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(
            titles, text="PMC Overwatch",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=_TEXT_PRIMARY,
        ).pack(anchor="w")
        ctk.CTkLabel(
            titles, text="Tarkov AI Companion",
            font=ctk.CTkFont(size=11), text_color=_TEXT_SECONDARY,
        ).pack(anchor="w")

        self._toggle_btn = ctk.CTkButton(
            inner, text="▶  Start Overwatch", width=190, height=42,
            corner_radius=10, font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=_ACCENT_GREEN, hover_color=_ACCENT_GREEN_HOVER,
            text_color="white", command=self._on_toggle,
        )
        self._toggle_btn.pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  AVATAR SECTION — large animated face
    # ══════════════════════════════════════════════════════════════════
    def _build_avatar_section(self) -> None:
        section = ctk.CTkFrame(
            self, corner_radius=12, fg_color=_BG_CARD,
            border_width=1, border_color=_BORDER,
        )
        section.pack(fill="x", padx=16, pady=(10, 0))

        # Canvas for the avatar + ring
        canvas_size = _AVATAR_SIZE + 24
        self._avatar_canvas = ctk.CTkCanvas(
            section, width=canvas_size, height=canvas_size,
            bg=_BG_CARD, highlightthickness=0,
        )
        self._avatar_canvas.pack(pady=(14, 4))

        # Load base avatar
        if _HAS_PIL and _AVATAR_PATH.exists():
            try:
                img = Image.open(_AVATAR_PATH).convert("RGBA")
                img = img.resize((_AVATAR_SIZE, _AVATAR_SIZE), Image.LANCZOS)

                # Circular mask
                mask = Image.new("L", (_AVATAR_SIZE, _AVATAR_SIZE), 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((0, 0, _AVATAR_SIZE - 1, _AVATAR_SIZE - 1), fill=255)
                img.putalpha(mask)

                self._avatar_pil = img
                self._render_avatar()
            except Exception:
                logger.exception("Failed to load avatar")

        # Status label under avatar
        self._avatar_status = ctk.CTkLabel(
            section, text="Offline", font=ctk.CTkFont(size=13, weight="bold"),
            text_color=_TEXT_MUTED,
        )
        self._avatar_status.pack(pady=(2, 12))

        # Start animation
        self._start_animation()

    def _render_avatar(self, mouth_open: float = 0.0, blink: bool = False) -> None:
        """Render the avatar with mouth and eye animations overlaid."""
        if self._avatar_pil is None:
            return

        # Start from base image
        frame = self._avatar_pil.copy()
        draw = ImageDraw.Draw(frame)
        cx, cy = _AVATAR_SIZE // 2, _AVATAR_SIZE // 2

        # ── Eye blink: draw dark overlay on eye area ──────────────────
        if blink:
            # Semi-transparent eyelid overlay
            eye_y = int(cy * 0.72)  # eyes are roughly 72% from top
            eye_w = int(_AVATAR_SIZE * 0.12)
            eye_h = int(_AVATAR_SIZE * 0.04)
            left_eye_x = int(cx - _AVATAR_SIZE * 0.15)
            right_eye_x = int(cx + _AVATAR_SIZE * 0.15)
            skin = (200, 175, 160, 220)
            draw.ellipse(
                [left_eye_x - eye_w, eye_y - eye_h,
                 left_eye_x + eye_w, eye_y + eye_h],
                fill=skin
            )
            draw.ellipse(
                [right_eye_x - eye_w, eye_y - eye_h,
                 right_eye_x + eye_w, eye_y + eye_h],
                fill=skin
            )

        # ── Mouth animation: draw open mouth ─────────────────────────
        if mouth_open > 0.1:
            mouth_y = int(cy + _AVATAR_SIZE * 0.22)
            mouth_w = int(_AVATAR_SIZE * 0.08 + mouth_open * _AVATAR_SIZE * 0.04)
            mouth_h = int(mouth_open * _AVATAR_SIZE * 0.06)
            mouth_color = (60, 20, 30, int(200 * mouth_open))
            draw.ellipse(
                [cx - mouth_w, mouth_y - mouth_h,
                 cx + mouth_w, mouth_y + mouth_h],
                fill=mouth_color
            )

        # Composite onto dark background
        bg = Image.new("RGBA", (_AVATAR_SIZE, _AVATAR_SIZE), (13, 17, 23, 255))
        bg.paste(frame, (0, 0), frame)
        final = bg.convert("RGB")

        self._avatar_photo = ImageTk.PhotoImage(final)

    def _draw_avatar_frame(self) -> None:
        """Draw the avatar + glow ring on the canvas."""
        c = self._avatar_canvas
        c.delete("all")
        canvas_size = _AVATAR_SIZE + 24
        cx, cy = canvas_size // 2, canvas_size // 2
        r = _AVATAR_SIZE // 2

        # Outer glow ring
        glow_r = r + 8
        pulse = (math.sin(self._anim_phase) + 1) * 0.5
        ring_w = 3 + int(pulse * 2)
        c.create_oval(
            cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
            outline=self._ring_color, width=ring_w,
        )

        # Avatar image
        if self._avatar_photo is not None:
            c.create_image(cx, cy, image=self._avatar_photo, anchor="center")
        else:
            c.create_oval(cx - r, cy - r, cx + r, cy + r,
                fill=_BG_SURFACE, outline=_BORDER, width=2)
            c.create_text(cx, cy, text="🎮", font=("", 48), fill=_TEXT_PRIMARY)

    # ── Animation loop ────────────────────────────────────────────────
    def _start_animation(self) -> None:
        if self._anim_job is not None:
            self.after_cancel(self._anim_job)
        self._animate()

    def _animate(self) -> None:
        """Main animation tick — runs at ~20fps."""
        if self.shutdown_event.is_set():
            return

        self._anim_phase += 0.12

        # ── Blink logic ───────────────────────────────────────────────
        self._blink_timer += 1
        if self._blink_state:
            # Blink lasts 3 frames
            if self._blink_timer > 3:
                self._blink_state = False
                self._blink_timer = 0
        else:
            # Random blink every 3-6 seconds (60-120 frames at 20fps)
            if self._blink_timer > random.randint(60, 120):
                self._blink_state = True
                self._blink_timer = 0

        # ── Mouth logic ───────────────────────────────────────────────
        if self._vis_mode == "speaking":
            # Mouth opens and closes with some randomness
            target = 0.4 + random.uniform(0, 0.6)
            self._mouth_open += (target - self._mouth_open) * 0.5
        elif self._vis_mode == "thinking":
            # Subtle mouth movement
            self._mouth_open += (0.15 - self._mouth_open) * 0.3
        else:
            # Close mouth
            self._mouth_open += (0.0 - self._mouth_open) * 0.4

        # Render new frame
        if _HAS_PIL and self._avatar_pil is not None:
            self._render_avatar(
                mouth_open=self._mouth_open,
                blink=self._blink_state,
            )

        self._draw_avatar_frame()
        self._anim_job = self.after(50, self._animate)  # 20fps

    # ══════════════════════════════════════════════════════════════════
    #  LOG
    # ══════════════════════════════════════════════════════════════════
    def _build_log(self) -> None:
        log_frame = ctk.CTkFrame(
            self, corner_radius=12, fg_color=_BG_CARD,
            border_width=1, border_color=_BORDER,
        )
        log_frame.pack(fill="both", expand=True, padx=16, pady=10)

        hdr = ctk.CTkFrame(log_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(
            hdr, text="📋  Activity Log",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_TEXT_SECONDARY,
        ).pack(side="left")

        self._log_box = ctk.CTkTextbox(
            log_frame, font=ctk.CTkFont(family="Menlo", size=12),
            corner_radius=8, fg_color=_BG_SURFACE,
            text_color=_TEXT_PRIMARY, state="disabled",
            wrap="word", border_width=0,
        )
        self._log_box.pack(fill="both", expand=True, padx=10, pady=(6, 10))

    # ══════════════════════════════════════════════════════════════════
    #  FOOTER
    # ══════════════════════════════════════════════════════════════════
    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(
            self, corner_radius=12, fg_color=_BG_CARD,
            border_width=1, border_color=_BORDER, height=44,
        )
        footer.pack(fill="x", padx=16, pady=(0, 14))
        footer.pack_propagate(False)

        inner = ctk.CTkFrame(footer, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=10)

        self._status_dot = ctk.CTkLabel(
            inner, text="●", font=ctk.CTkFont(size=12),
            text_color=_TEXT_MUTED, width=16,
        )
        self._status_dot.pack(side="left", padx=(0, 6))

        self._status_label = ctk.CTkLabel(
            inner, text="Offline", font=ctk.CTkFont(size=12),
            text_color=_TEXT_SECONDARY,
        )
        self._status_label.pack(side="left")

        ctk.CTkLabel(
            inner, text="v2.3", font=ctk.CTkFont(size=11), text_color=_TEXT_MUTED,
        ).pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  PUBLIC API (thread-safe)
    # ══════════════════════════════════════════════════════════════════
    def set_toggle_callback(self, cb: Callable[[bool], None]) -> None:
        self._toggle_callback = cb

    def register_thread(self, t: threading.Thread) -> None:
        self._threads.append(t)

    def log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.after(0, self._append_log, f"[{ts}]  {message}\n")

    def set_status(self, text: str) -> None:
        self.after(0, self._update_status, text)

    def force_toggle_off(self) -> None:
        self.after(0, self._do_force_toggle_off)

    def set_vis_mode(self, mode: str) -> None:
        self.after(0, self._do_set_vis_mode, mode)

    # ══════════════════════════════════════════════════════════════════
    #  INTERNAL
    # ══════════════════════════════════════════════════════════════════
    def _append_log(self, line: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", line)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _do_set_vis_mode(self, mode: str) -> None:
        self._vis_mode = mode
        self._ring_color = _RING_COLORS.get(mode, _RING_COLORS["idle"])
        labels = {
            "idle": ("Offline", _TEXT_MUTED),
            "listening": ("🎧 Listening…", _ACCENT_GREEN),
            "speaking": ("🎙 Speaking…", _ACCENT_CYAN),
            "thinking": ("🧠 Thinking…", _ACCENT_AMBER),
        }
        text, color = labels.get(mode, ("Offline", _TEXT_MUTED))
        self._avatar_status.configure(text=text, text_color=color)

    def _update_status(self, text: str) -> None:
        self._status_label.configure(text=text)
        lower = text.lower()
        if "listening" in lower:
            self._set_dot_color(_ACCENT_GREEN, True)
            self._do_set_vis_mode("listening")
        elif "speaking" in lower:
            self._set_dot_color(_ACCENT_CYAN, True)
            self._do_set_vis_mode("speaking")
        elif "thinking" in lower:
            self._set_dot_color(_ACCENT_AMBER, True)
            self._do_set_vis_mode("thinking")
        elif "offline" in lower or "error" in lower:
            self._set_dot_color(_TEXT_MUTED, False)
            self._do_set_vis_mode("idle")
        else:
            self._set_dot_color(_ACCENT_AMBER, False)

    def _set_dot_color(self, color: str, pulse: bool = False) -> None:
        self._status_color = color
        self._status_dot.configure(text_color=color)
        if self._pulse_job:
            self.after_cancel(self._pulse_job)
            self._pulse_job = None
        if pulse:
            self._pulse_visible = True
            self._pulse_tick()

    def _pulse_tick(self) -> None:
        if self.shutdown_event.is_set():
            return
        self._pulse_visible = not self._pulse_visible
        self._status_dot.configure(
            text_color=self._status_color if self._pulse_visible else _BG_CARD
        )
        self._pulse_job = self.after(600, self._pulse_tick)

    def _do_force_toggle_off(self) -> None:
        self._is_running = False
        self._toggle_btn.configure(
            text="▶  Start Overwatch",
            fg_color=_ACCENT_GREEN, hover_color=_ACCENT_GREEN_HOVER,
        )
        self._update_status("Offline")

    def _on_toggle(self) -> None:
        self._is_running = not self._is_running
        if self._is_running:
            self._toggle_btn.configure(
                text="⏹  Stop Overwatch",
                fg_color=_ACCENT_RED, hover_color=_ACCENT_RED_HOVER,
            )
            self._update_status("Starting…")
            self.log("Overwatch activated ✅")
        else:
            self._toggle_btn.configure(
                text="▶  Start Overwatch",
                fg_color=_ACCENT_GREEN, hover_color=_ACCENT_GREEN_HOVER,
            )
            self._update_status("Offline")
            self.log("Overwatch deactivated ⛔")
        if self._toggle_callback:
            self._toggle_callback(self._is_running)

    def _on_closing(self) -> None:
        logger.info("Shutdown requested")
        if self._pulse_job:
            self.after_cancel(self._pulse_job)
        if self._anim_job:
            self.after_cancel(self._anim_job)
        self.shutdown_event.set()
        for t in self._threads:
            t.join(timeout=3.0)
        logger.info("Shutdown complete")
        self.destroy()


if __name__ == "__main__":
    app = OverwatchGUI()
    app.mainloop()
