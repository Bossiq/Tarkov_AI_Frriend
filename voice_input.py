import sounddevice as sd
import soundfile as sf
import numpy as np
import time

class VoiceInput:
    def __init__(self, samplerate=44100, channels=1):
        self.samplerate = samplerate
        self.channels = channels

    def record_audio(self, duration=5, timeout=5, phrase_time_limit=10, output_filename="temp_audio.wav"):
        """Records audio for a given duration and saves it to a WAV file."""
        # Use timeout or phrase_time_limit properties to dictate maximum sounddevice listening duration
        max_duration = min(timeout, phrase_time_limit) if timeout and phrase_time_limit else duration
        print(f"Recording audio for {max_duration} seconds... Speak now!")
        try:
            # sd.rec is blocking until the recording is finished if we wait for it.
            myrecording = sd.rec(int(duration * self.samplerate), samplerate=self.samplerate, channels=self.channels, dtype='float64')
            sd.wait()  # Wait until recording is finished
            print("Recording stopped. Saving...")
            sf.write(output_filename, myrecording, self.samplerate)
            print(f"Saved audio to {output_filename}")
            return output_filename
        except Exception as e:
            print(f"Error recording audio: {e}")
            return None

if __name__ == "__main__":
    # Simple test
    vi = VoiceInput()
    vi.record_audio(duration=3, output_filename="test_audio.wav")
