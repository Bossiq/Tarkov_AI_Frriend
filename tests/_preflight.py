"""Pre-flight system check — verifies everything before live testing."""
import os
import sys

# Force UTF-8 on Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

print("=" * 60)
print("  PMC Overwatch — Pre-flight Check")
print("=" * 60)

# 1. Environment variables
print("\n── Environment Config ──")
env_keys = [
    ("GROQ_API_KEY", True),
    ("GEMINI_API_KEY", True),
    ("OLLAMA_MODEL", False),
    ("INPUT_MODE", False),
    ("SCREEN_CAPTURE", False),
    ("SFX_ENABLED", False),
    ("DASHBOARD_PORT", False),
    ("TWITCH_TOKEN", True),
    ("TTS_VOICE", False),
    ("WHISPER_MODEL", False),
]
for key, sensitive in env_keys:
    val = os.getenv(key, "")
    if sensitive and val:
        display = val[:4] + "***" + val[-4:] if len(val) > 8 else "***"
    elif val:
        display = val
    else:
        display = "NOT SET"
    status = "✅" if val else "⚠️"
    print(f"  {status} {key:30s} = {display}")

# 2. GPU / PyTorch
print("\n── GPU / PyTorch ──")
try:
    import torch
    print(f"  ✅ PyTorch {torch.__version__}")
    if torch.cuda.is_available():
        print(f"  ✅ CUDA: {torch.cuda.get_device_name(0)}")
    else:
        print("  ⚠️ CUDA not available (Whisper will use CPU)")
except ImportError:
    print("  ❌ PyTorch not installed")

# 3. Audio devices
print("\n── Audio Devices ──")
try:
    import sounddevice as sd
    devices = sd.query_devices()
    inputs = [d for d in devices if d["max_input_channels"] > 0]
    print(f"  ✅ {len(inputs)} input devices found")
    for d in inputs[:3]:
        print(f"     [{d['index']}] {d['name']}")
except Exception as e:
    print(f"  ❌ Audio error: {e}")

# 4. Model files
print("\n── Model Files ──")
base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
models_dir = os.path.join(base, "models")
model_files = {
    "kokoro-v1.0.onnx": os.path.join(models_dir, "kokoro-v1.0.onnx"),
    "voices-v1.0.bin": os.path.join(models_dir, "voices-v1.0.bin"),
    "altyn_boss.fbx": os.path.join(models_dir, "altyn_boss.fbx"),
    "rpk_gold.glb": os.path.join(models_dir, "rpk_gold.glb"),
}
for name, path in model_files.items():
    if os.path.exists(path):
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"  ✅ {name:25s} ({size_mb:.1f} MB)")
    else:
        print(f"  ❌ {name:25s} MISSING")

# 5. Animation files
print("\n── Animations ──")
anim_dir = os.path.join(base, "assets", "animations")
if os.path.isdir(anim_dir):
    anims = [f for f in os.listdir(anim_dir) if f.endswith(".fbx")]
    print(f"  ✅ {len(anims)} FBX animations found")
    for f in sorted(anims):
        size_mb = os.path.getsize(os.path.join(anim_dir, f)) / (1024 * 1024)
        print(f"     {f:30s} ({size_mb:.1f} MB)")
else:
    print("  ❌ animations directory not found")

# 6. HTML overlays
print("\n── HTML Overlays ──")
html_files = {
    "dashboard_ui.html": os.path.join(base, "assets", "dashboard_ui.html"),
    "mascot_3d.html": os.path.join(base, "assets", "mascot_3d.html"),
    "mascot.html": os.path.join(base, "assets", "mascot.html"),
    "avatar_3d.html": os.path.join(base, "assets", "avatar_3d.html"),
}
for name, path in html_files.items():
    if os.path.exists(path):
        size_kb = os.path.getsize(path) / 1024
        print(f"  ✅ {name:25s} ({size_kb:.0f} KB)")
    else:
        print(f"  ❌ {name:25s} MISSING")

# 7. Whisper model
print("\n── Whisper STT ──")
try:
    whisper_model = os.getenv("WHISPER_MODEL", "small")
    print(f"  Model: {whisper_model}")
    cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    if os.path.isdir(cache_dir):
        whisper_dirs = [d for d in os.listdir(cache_dir) if "whisper" in d.lower()]
        if whisper_dirs:
            print(f"  ✅ Whisper cache found: {', '.join(whisper_dirs[:2])}")
        else:
            print("  ⚠️ No cached Whisper model (will download on first run)")
    else:
        print("  ⚠️ HuggingFace cache not found (will download on first run)")
except Exception as e:
    print(f"  ❌ Error: {e}")

# 8. Ollama check
print("\n── Ollama ──")
try:
    import subprocess
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/NH"],
        capture_output=True, text=True, timeout=5
    )
    if "ollama.exe" in result.stdout.lower():
        print("  ✅ Ollama is running")
    else:
        print("  ⚠️ Ollama not running (will auto-start if installed)")
except Exception:
    print("  ⚠️ Could not check Ollama status")

print("\n" + "=" * 60)
print("  Pre-flight check complete!")
print("=" * 60)
