import os
from google import genai
from google.genai import types

class Brain:
    def __init__(self):
        # API key is automatically picked up from GEMINI_API_KEY env var
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("WARNING: GEMINI_API_KEY not found in environment variables.")
            
        self.client = genai.Client(api_key=api_key)
        self.system_instruction = (
            "You are SCAV-E, an AI companion for an Escape from Tarkov Twitch streamer. "
            "You have the personality of a Russian Scav. You are gritty, sarcastic, "
            "and use common Scav slang (e.g., 'Blyat', 'Cheeki Breeki', 'Opachki', 'Devochka', 'Dickie Needles'). "
            "Keep your responses concise and impactful for text-to-speech."
        )

    def generate_response(self, text_prompt=None, image_path=None, audio_path=None):
        """Generates a response from the GenAI model using any combination of text, images, and audio."""
        contents = []

        if image_path and os.path.exists(image_path):
            print(f"Uploading image: {image_path}")
            try:
                image_file = self.client.files.upload(file=image_path)
                contents.append(image_file)
            except Exception as e:
                print(f"Error uploading image: {e}")

        if audio_path and os.path.exists(audio_path):
            print(f"Uploading audio: {audio_path}")
            try:
                audio_file = self.client.files.upload(file=audio_path)
                contents.append(audio_file)
            except Exception as e:
                print(f"Error uploading audio: {e}")

        if text_prompt:
            contents.append(text_prompt)

        if not contents:
            return "No input provided to the system."

        try:
            print("Generating GenAI response...")
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                    temperature=0.8,
                )
            )
            return response.text
        except Exception as e:
            print(f"GenAI Error: {e}")
            return "Server error, blyat. Could not connect to the network."

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    brain = Brain()
    # Test text prompt only
    print(brain.generate_response(text_prompt="Say hello like a true Scav!"))
