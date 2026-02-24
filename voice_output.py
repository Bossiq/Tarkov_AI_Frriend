import pyttsx3

class VoiceOutput:
    def __init__(self):
        try:
            self.engine = pyttsx3.init()
            # You can set properties here, e.g. voice, rate, volume
            self.engine.setProperty('rate', 150)
            self.engine.setProperty('volume', 1.0)
        except Exception as e:
            print(f"Failed to initialize pyttsx3: {e}")
            self.engine = None
        
    def speak(self, text):
        """Synthesizes text to speech verbally."""
        print(f"SCAV-E Output: {text}")
        if self.engine:
            try:
                self.engine.say(text)
                self.engine.runAndWait()
            except Exception as e:
                print(f"TTS Error: {e}")
        else:
            print("[TTS Engine not initialized. Output is console only.]")

if __name__ == "__main__":
    vo = VoiceOutput()
    vo.speak("Blyat! Cheeky breeky iv damke!")
