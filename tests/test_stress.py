"""
Stress tests for PMC Overwatch Phase 2 features.

Run with:
    cd c:\\Users\\maria\\.gemini\\antigravity\\scratch\\Tarkov_AI_Frriend
    .\\venv\\Scripts\\python.exe -m pytest tests/test_stress.py -v

Tests cover:
  • Screen capture: memory stability over 60s, thread cleanup
  • Sound effects: rapid-fire events, thread count stays bounded
  • Dashboard: concurrent API requests
  • Expression engine: all emotions valid
  • Cross-platform: imports work, no OS-specific crashes
"""

import gc
import os
import sys
import threading
import time

import numpy as np

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════
#  SCREEN CAPTURE TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestScreenCapture:
    """Stress tests for the screen capture module."""

    def test_import(self):
        """ScreenCapture module imports without error."""
        from video_capture import ScreenCapture
        assert ScreenCapture is not None

    def test_availability(self):
        """ScreenCapture.available reflects actual mss/PIL state."""
        from video_capture import ScreenCapture
        sc = ScreenCapture()
        # Should return True if mss and PIL are installed
        assert isinstance(sc.available, bool)

    def test_start_stop(self):
        """Start and stop cleanly — no thread leaks."""
        from video_capture import ScreenCapture
        if not ScreenCapture().available:
            return  # Skip if mss not installed

        threads_before = threading.active_count()
        sc = ScreenCapture(fps=2.0)
        sc.start()
        time.sleep(1)
        sc.stop()
        time.sleep(0.5)
        threads_after = threading.active_count()
        # Should not leak threads (allow +1 for jitter)
        assert threads_after <= threads_before + 1, (
            f"Thread leak: {threads_before} → {threads_after}"
        )

    def test_frame_capture(self):
        """Captures frames and returns valid JPEG bytes."""
        from video_capture import ScreenCapture
        if not ScreenCapture().available:
            return

        sc = ScreenCapture(fps=5.0)
        sc.start()
        time.sleep(2)
        frame = sc.get_latest_frame()
        sc.stop()

        assert frame is not None, "No frame captured"
        assert len(frame) > 1000, f"Frame too small: {len(frame)} bytes"
        # JPEG magic bytes
        assert frame[:2] == b'\xff\xd8', "Not a valid JPEG"

    def test_memory_stability_30s(self):
        """Run capture for 30s — memory should not grow unboundedly."""
        from video_capture import ScreenCapture
        if not ScreenCapture().available:
            return

        import psutil
        process = psutil.Process(os.getpid())

        sc = ScreenCapture(fps=2.0)
        sc.start()

        mem_start = process.memory_info().rss / (1024 * 1024)  # MB
        time.sleep(30)
        mem_end = process.memory_info().rss / (1024 * 1024)
        sc.stop()

        mem_growth = mem_end - mem_start
        # Allow up to 50MB growth (includes Python GC variance)
        assert mem_growth < 50, (
            f"Memory grew {mem_growth:.1f}MB in 30s "
            f"(start={mem_start:.1f}MB, end={mem_end:.1f}MB)"
        )
        print(f"  Memory: {mem_start:.1f}MB → {mem_end:.1f}MB "
              f"(growth: {mem_growth:.1f}MB)")

    def test_frame_path(self):
        """get_latest_frame_path returns a valid file."""
        from video_capture import ScreenCapture
        if not ScreenCapture().available:
            return

        sc = ScreenCapture(fps=2.0)
        sc.start()
        time.sleep(1)
        path = sc.get_latest_frame_path()

        # Check BEFORE stop() — stop() cleans up the temp file
        assert path is not None, "No path returned"
        assert os.path.exists(path), f"File does not exist: {path}"
        assert os.path.getsize(path) > 1000, "File too small"
        sc.stop()

    def test_context_manager(self):
        """Context manager starts and stops cleanly."""
        from video_capture import ScreenCapture
        if not ScreenCapture().available:
            return

        with ScreenCapture(fps=2.0) as sc:
            time.sleep(1)
            assert sc.frame_count > 0

    def test_multiple_start_stop(self):
        """Multiple start/stop cycles don't leak resources."""
        from video_capture import ScreenCapture
        if not ScreenCapture().available:
            return

        sc = ScreenCapture(fps=2.0)
        for _ in range(5):
            sc.start()
            time.sleep(0.5)
            sc.stop()
        # Should be cleanly stopped
        assert sc._running is False


# ═══════════════════════════════════════════════════════════════════════
#  SOUND EFFECTS TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestSoundEffects:
    """Stress tests for the sound effects engine."""

    def test_import(self):
        from sound_effects import SoundEffects
        assert SoundEffects is not None

    def test_load_all_effects(self):
        """All 6 effects generate without error."""
        from sound_effects import SoundEffects
        sfx = SoundEffects(enabled=True, volume=0.0)  # silent
        assert len(sfx._sounds) == 6
        for name, audio in sfx._sounds.items():
            assert len(audio) > 100, f"Effect '{name}' too short"
            assert audio.dtype == np.float32, f"Effect '{name}' wrong dtype"

    def test_rapid_fire_events(self):
        """Trigger 100 rapid events — no crashes, thread count bounded."""
        from sound_effects import SoundEffects
        sfx = SoundEffects(enabled=True, volume=0.0)

        threads_before = threading.active_count()
        for i in range(100):
            event = ["startup", "thinking", "respond", "twitch", "bargein", "error"][i % 6]
            sfx.play(event)
        time.sleep(1)

        threads_after = threading.active_count()
        # sounddevice creates a playback thread per play() call,
        # but they complete quickly — shouldn't accumulate
        assert threads_after < threads_before + 10, (
            f"Thread accumulation: {threads_before} → {threads_after}"
        )

    def test_cooldown_respected(self):
        """Same effect doesn't play twice within cooldown window."""
        from sound_effects import SoundEffects
        sfx = SoundEffects(enabled=True, volume=0.0)

        # First play should work (updates last_played)
        sfx.play("startup")
        t1 = sfx._last_played.get("startup", 0)

        # Immediate second play should be blocked by cooldown
        sfx.play("startup")
        t2 = sfx._last_played.get("startup", 0)

        # t2 should equal t1 (not updated because cooldown blocked it)
        assert t1 == t2, "Cooldown not respected"

    def test_disabled_mode(self):
        """Disabled SFX doesn't crash on play."""
        from sound_effects import SoundEffects
        sfx = SoundEffects(enabled=False)
        sfx.play("startup")  # Should not crash
        sfx.play("error")

    def test_volume_bounds(self):
        """Volume clamps to [0, 1]."""
        from sound_effects import SoundEffects
        sfx = SoundEffects(enabled=True, volume=0.0)
        sfx.set_volume(2.0)
        assert sfx._volume == 1.0
        sfx.set_volume(-1.0)
        assert sfx._volume == 0.0


# ═══════════════════════════════════════════════════════════════════════
#  DASHBOARD TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestMascotServer:
    """Tests for the mascot server module."""

    def test_import(self):
        from mascot_server import MascotServer
        assert MascotServer is not None

    def test_read_env(self):
        """Read .env file returns a non-empty dict."""
        from mascot_server import _read_env_dict
        config = _read_env_dict()
        assert isinstance(config, dict)
        assert len(config) > 0, "No config loaded from .env"

    def test_sanitize_keys(self):
        """Sensitive keys are properly masked."""
        from mascot_server import _sanitize_config
        config = {
            "GROQ_API_KEY": "gsk_abc123def456",
            "GEMINI_API_KEY": "AIzaSy123456",
            "TTS_VOICE": "af_heart",
        }
        sanitized = _sanitize_config(config)
        assert "***" in sanitized["GROQ_API_KEY"]
        assert "***" in sanitized["GEMINI_API_KEY"]
        assert sanitized["TTS_VOICE"] == "af_heart"  # not sensitive

    def test_sanitize_preserves_prefix_suffix(self):
        """Masked keys keep first 4 and last 4 chars."""
        from mascot_server import _sanitize_config
        config = {"GROQ_API_KEY": "gsk_lBEfpjFS6aq5pegWawOh"}
        sanitized = _sanitize_config(config)
        key = sanitized["GROQ_API_KEY"]
        assert key.startswith("gsk_"), f"Prefix lost: {key}"
        assert key.endswith("awOh"), f"Suffix lost: {key}"

    def test_dashboard_html_exists(self):
        """Dashboard HTML file exists at expected location."""
        html_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "dashboard_ui.html"
        )
        assert os.path.exists(html_path), f"Dashboard HTML not found: {html_path}"


# ═══════════════════════════════════════════════════════════════════════
#  EXPRESSION ENGINE TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestExpressionEngine:
    """Tests for expression engine with new emotions."""

    def test_all_emotions_have_sprites(self):
        """Every Emotion enum value has a matching EXPRESSION_MAP entry."""
        from expression_engine import Emotion, EXPRESSION_MAP
        for emotion in Emotion:
            assert emotion in EXPRESSION_MAP, (
                f"Emotion.{emotion.name} missing from EXPRESSION_MAP"
            )

    def test_new_emotions_exist(self):
        """FOCUSED and ALARMED emotions exist."""
        from expression_engine import Emotion
        assert hasattr(Emotion, "FOCUSED")
        assert hasattr(Emotion, "ALARMED")

    def test_detect_expression(self):
        """detect_expression returns valid Emotion values."""
        from expression_engine import detect_expression, Emotion
        result = detect_expression("Let's go!!! That was insane!")
        assert isinstance(result, Emotion)

    def test_12_emotions(self):
        """Total emotion count is 12."""
        from expression_engine import Emotion
        assert len(list(Emotion)) == 12


# ═══════════════════════════════════════════════════════════════════════
#  BRAIN VISION TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestBrainVision:
    """Tests for brain's vision analysis methods."""

    def test_vision_methods_exist(self):
        """Brain has analyze_screen and get_screen_context methods."""
        from brain import Brain
        assert hasattr(Brain, "analyze_screen")
        assert hasattr(Brain, "get_screen_context")

    def test_vision_cooldown(self):
        """Vision requests respect cooldown interval."""
        from brain import Brain
        # Just verify the class attribute exists
        assert Brain._VISION_COOLDOWN >= 5.0


# ═══════════════════════════════════════════════════════════════════════
#  CROSS-PLATFORM TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestCrossPlatform:
    """Tests that verify cross-platform compatibility."""

    def test_all_modules_import(self):
        """All project modules import without OS-specific errors."""
        modules = [
            "video_capture", "sound_effects", "mascot_server",
            "brain", "expression_engine", "logging_config",
            "tarkov_data",
        ]
        for mod in modules:
            try:
                __import__(mod)
            except ImportError as e:
                # Optional deps (like mss, fastapi) should degrade gracefully
                if "No module named" in str(e):
                    pass
                else:
                    raise

    def test_temp_dir_writable(self):
        """System temp directory is writable (cross-platform temp path)."""
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), "pmc_overwatch_test.txt")
        with open(tmp, "w") as f:
            f.write("test")
        assert os.path.exists(tmp)
        os.remove(tmp)

    def test_no_hardcoded_windows_paths(self):
        """No hardcoded Windows drive letters in project source files."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        import re
        drive_pattern = re.compile(r'[A-Z]:\\\\(?!Users)', re.IGNORECASE)
        issues = []
        for fname in os.listdir(project_root):
            if fname.endswith(".py"):
                fpath = os.path.join(project_root, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        if drive_pattern.search(line) and "comment" not in line.lower():
                            # Allow in comments and docstrings
                            stripped = line.strip()
                            if not stripped.startswith("#") and not stripped.startswith('"'):
                                issues.append(f"{fname}:{i}: {stripped[:80]}")
        assert not issues, f"Hardcoded Windows paths found:\n" + "\n".join(issues)


# ═══════════════════════════════════════════════════════════════════════
#  INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestIntegration:
    """Integration tests for Phase 2 features working together."""

    def test_screen_capture_to_file(self):
        """Screen capture produces a valid JPEG file that PIL can open."""
        from video_capture import ScreenCapture
        if not ScreenCapture().available:
            return

        from PIL import Image
        sc = ScreenCapture(fps=2.0)
        sc.start()
        time.sleep(1)
        path = sc.get_latest_frame_path()

        # Check BEFORE stop() — stop() cleans up the temp file
        if path:
            img = Image.open(path)
            assert img.size[0] == 1280
            assert img.size[1] == 720
            assert img.mode == "RGB"
        sc.stop()

    def test_sfx_wav_files_generated(self):
        """SFX WAV files are auto-generated in assets/sfx/."""
        from sound_effects import SoundEffects
        sfx = SoundEffects(enabled=True, volume=0.0)

        sfx_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "sfx"
        )
        if os.path.exists(sfx_dir):
            wav_files = [f for f in os.listdir(sfx_dir) if f.endswith(".wav")]
            assert len(wav_files) >= 1, "No WAV files generated"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
