import pyttsx3

class VoiceOutput:
    def __init__(self):
        # Defers pyttsx3 init to avoid cross-thread COM errors on Windows
        pass
        
    def speak(self, text):
        """Synthesizes text to speech verbally."""
        print(f"SCAV-E Output: {text}")
        try:
            # Initialize engine locally so the background thread can run it safely
            engine = pyttsx3.init()
            # Enforce the requested TTS properties
            engine.setProperty('rate', 150)
            engine.setProperty('volume', 1.0)
            
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"TTS Error: {e}")

if __name__ == "__main__":
    vo = VoiceOutput()
    vo.speak("Blyat! Cheeky breeky iv damke!")
