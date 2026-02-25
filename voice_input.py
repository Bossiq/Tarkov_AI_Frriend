import sounddevice as sd
import numpy as np
import soundfile as sf
import time

class VoiceInput:
    def __init__(self, samplerate=16000, channels=1, threshold=0.02, silence_duration=1.5):
        self.samplerate = samplerate
        self.channels = channels
        self.threshold = threshold
        self.silence_duration = silence_duration

    def listen(self, output_filename="temp_audio.wav"):
        """Listens continuously using a custom Volume Activity Detector."""
        print('\n[PMC Overwatch] Listening...')
        
        recorded_frames = []
        is_recording = False
        silence_start_time = None
        
        try:
            # Open sounddevice stream
            with sd.InputStream(samplerate=self.samplerate, channels=self.channels, dtype='float32') as stream:
                while True:
                    # Read a small chunk of audio (e.g., 0.1 seconds)
                    chunk, overflowed = stream.read(int(self.samplerate * 0.1))
                    
                    # Calculate RMS energy (volume)
                    rms = np.sqrt(np.mean(chunk**2))
                    
                    if rms > self.threshold:
                        if not is_recording:
                            print('[PMC Overwatch] Speech detected, receiving transmission...')
                            is_recording = True
                        recorded_frames.append(chunk)
                        silence_start_time = None
                    elif is_recording:
                        recorded_frames.append(chunk)
                        if silence_start_time is None:
                            silence_start_time = time.time()
                        elif time.time() - silence_start_time > self.silence_duration:
                            # Silence duration exceeded, stop recording
                            break

            if recorded_frames:
                print('[PMC Overwatch] Processing audio transmission...')
                audio_data = np.concatenate(recorded_frames, axis=0)
                sf.write(output_filename, audio_data, self.samplerate)
                return output_filename
            return None
            
        except Exception as e:
            print(f"Error in VAD listening loop: {e}")
            return None

if __name__ == "__main__":
    vi = VoiceInput()
    vi.listen(output_filename="test_audio.wav")
