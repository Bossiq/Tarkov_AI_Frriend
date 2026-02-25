import subprocess


class VoiceOutput:
    def __init__(self, gui_callback=None):
        """
        Initializes the VoiceOutput using macOS native 'say' command.

        Args:
            gui_callback: Optional callable(str) to send speech text to the GUI log.
        """
        self.gui_callback = gui_callback

    def speak(self, text):
        """Speaks the given text aloud using the macOS 'say' command."""
        if not text or not text.strip():
            return

        if self.gui_callback:
            self.gui_callback(f"🎙 PMC: {text}")

        try:
            subprocess.run(['say', text], check=True)
        except FileNotFoundError:
            print("Error: 'say' command not found. Are you on macOS?")
        except Exception as e:
            print(f"TTS Error: {e}")


if __name__ == "__main__":
    vo = VoiceOutput()
    vo.speak("Affirmative. Holding position.")
