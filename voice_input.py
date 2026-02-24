import speech_recognition as sr
import time

class VoiceInput:
    def __init__(self):
        self.recognizer = sr.Recognizer()

    def record_audio(self, timeout=5, phrase_time_limit=10, output_filename="temp_audio.wav"):
        """Records audio from the microphone and saves it to a WAV file."""
        with sr.Microphone() as source:
            print('\n[SCAV-E] Microphone active. Speak now...')
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            try:
                # Listen for the user's input
                audio_data = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
                print('[SCAV-E] Processing audio...')
                
                # Write audio to a WAV file
                with open(output_filename, "wb") as f:
                    f.write(audio_data.get_wav_data())
                
                return output_filename
            except sr.WaitTimeoutError:
                print("Listening timed out. No speech detected.")
                return None
            except Exception as e:
                print(f"Error recording audio: {e}")
                return None

if __name__ == "__main__":
    # Simple test
    vi = VoiceInput()
    vi.record_audio(timeout=5, phrase_time_limit=10, output_filename="test_audio.wav")

if __name__ == "__main__":
    # Simple test
    vi = VoiceInput()
    vi.record_audio(duration=3, output_filename="test_audio.wav")
