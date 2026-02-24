import edge_tts
import pygame
import asyncio
import os

class VoiceOutput:
    def __init__(self, voice="en-US-ChristopherNeural"):
        self.voice = voice
        pygame.mixer.init()

    async def _generate_audio(self, text, output_file):
        """Generates an MP3 file from text using edge-tts."""
        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(output_file)

    def speak(self, text):
        """Generates and plays speech audio."""
        print(f"SCAV-E Output: {text}")
        output_file = "scav_output.mp3"
        
        try:
            # Generate the audio blockingly (since speak is called from a thread in main.py)
            asyncio.run(self._generate_audio(text, output_file))
            
            # Play the audio using pygame
            pygame.mixer.music.load(output_file)
            pygame.mixer.music.play()
            
            # Wait for playback to finish
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
                
            # Unload the file so it can be overwritten next time
            pygame.mixer.music.unload()
            
        except Exception as e:
            print(f"TTS Error: {e}")

if __name__ == "__main__":
    vo = VoiceOutput()
    vo.speak("Blyat! Cheeky breeky iv damke!")
