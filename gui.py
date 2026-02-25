import customtkinter as ctk
from datetime import datetime


class OverwatchGUI(ctk.CTk):
    """
    Modern dark-mode desktop GUI for the Tarkov AI PMC Overwatch system.
    Provides a toggle button, status label, and scrollable log.
    """

    def __init__(self):
        super().__init__()

        # ── Window setup ──────────────────────────────────────────────
        self.title("PMC Overwatch — Tarkov AI")
        self.geometry("720x600")
        self.minsize(520, 420)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Callback set by main.py when toggling overwatch
        self._toggle_callback = None
        self._is_running = False

        # ── Layout ────────────────────────────────────────────────────
        self._build_header()
        self._build_log()
        self._build_footer()

    # ── Header ────────────────────────────────────────────────────────
    def _build_header(self):
        header = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(18, 0))

        title_label = ctk.CTkLabel(
            header,
            text="⚔  PMC Overwatch",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        title_label.pack(side="left")

        self.toggle_btn = ctk.CTkButton(
            header,
            text="▶  Start Overwatch",
            width=180,
            height=38,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#2b8a3e",
            hover_color="#237032",
            command=self._on_toggle,
        )
        self.toggle_btn.pack(side="right")

    # ── Scrollable log ────────────────────────────────────────────────
    def _build_log(self):
        self.log_box = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Menlo", size=13),
            corner_radius=8,
            state="disabled",
            wrap="word",
        )
        self.log_box.pack(fill="both", expand=True, padx=20, pady=12)

    # ── Footer / status bar ───────────────────────────────────────────
    def _build_footer(self):
        footer = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(0, 14))

        self.status_label = ctk.CTkLabel(
            footer,
            text="Status: Offline",
            font=ctk.CTkFont(size=13),
            text_color="#adb5bd",
        )
        self.status_label.pack(side="left")

    # ── Public API (thread-safe) ──────────────────────────────────────
    def set_toggle_callback(self, callback):
        """Register a callback(is_running: bool) invoked when the user clicks toggle."""
        self._toggle_callback = callback

    def log(self, message: str):
        """Append a timestamped message to the log. Safe to call from any thread."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}]  {message}\n"
        # Schedule on the main thread
        self.after(0, self._append_log, line)

    def set_status(self, text: str):
        """Update the status label. Safe to call from any thread."""
        self.after(0, self._update_status, text)

    # ── Internal helpers ──────────────────────────────────────────────
    def _append_log(self, line: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _update_status(self, text: str):
        self.status_label.configure(text=f"Status: {text}")

    def _on_toggle(self):
        self._is_running = not self._is_running

        if self._is_running:
            self.toggle_btn.configure(
                text="⏹  Stop Overwatch",
                fg_color="#c92a2a",
                hover_color="#a51d1d",
            )
            self.set_status("Listening...")
            self.log("Overwatch activated ✅")
        else:
            self.toggle_btn.configure(
                text="▶  Start Overwatch",
                fg_color="#2b8a3e",
                hover_color="#237032",
            )
            self.set_status("Offline")
            self.log("Overwatch deactivated ⛔")

        if self._toggle_callback:
            self._toggle_callback(self._is_running)


if __name__ == "__main__":
    app = OverwatchGUI()
    app.mainloop()
