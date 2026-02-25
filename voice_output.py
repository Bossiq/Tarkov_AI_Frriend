import os
import time
import subprocess
import soundfile as sf
import sounddevice as sd

class VoiceOutput:
    def __init__(self, model="piper/operator.onnx", executable="piper/piper.exe"):
        self.model = model
        self.executable = executable

    def speak(self, text):
        """Generates and plays speech audio via offline Piper TTS CLI."""
        print(f"PMC Output: {text}")
        
        unique_filename = f'pmc_output_{int(time.time())}.wav'
        
        try:
            # Generate the audio blockingly using Piper TTS CLI
            subprocess.run(
                [self.executable, '--model', self.model, '--output_file', unique_filename],
                input=text.encode('utf-8'),
                check=True
            )
            
            # Play the audio using sounddevice blockingly
            data, fs = sf.read(unique_filename)
            sd.play(data, fs)
            sd.wait()
            
        except Exception as e:
            print(f"TTS Error: {e}")
        finally:
            if os.path.exists(unique_filename):
                try:
                    os.remove(unique_filename)
                except Exception as cleanup_error:
                    pass

if __name__ == "__main__":
    vo = VoiceOutput()
    vo.speak("Affirmative. Holding position.")
