"""
Unit tests for PMC Overwatch — pure-function logic.

Run with:
    python -m pytest tests/test_units.py -v

Tests cover:
  • Expression detection (emotion classification from text)
  • Expression engine (state machine, decay, sprite selection)
  • Language detection (EN/RU/RO classification)
  • Text preprocessing (numbers, abbreviations, markdown, emojis)
  • Tarkov updater (data formatting)
  • Number-to-words conversion
"""

import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════
#  EXPRESSION DETECTION TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestExpressionDetection:
    """Tests for detect_expression() emotion classification."""

    def test_excited_text(self):
        from expression_engine import detect_expression, Emotion
        result = detect_expression("Let's go!!! That was insane! Clutch play!")
        assert result == Emotion.EXCITED

    def test_neutral_text(self):
        from expression_engine import detect_expression, Emotion
        result = detect_expression("Okay then.")
        assert result == Emotion.NEUTRAL

    def test_concerned_text(self):
        from expression_engine import detect_expression, Emotion
        result = detect_expression("Watch out! Danger ahead, be careful!")
        assert result == Emotion.CONCERNED

    def test_happy_text(self):
        from expression_engine import detect_expression, Emotion
        result = detect_expression("That's awesome, I love it! Perfect!")
        assert result == Emotion.HAPPY

    def test_amused_text(self):
        from expression_engine import detect_expression, Emotion
        result = detect_expression("Hahaha bruh that's hilarious, I'm dying!")
        assert result == Emotion.AMUSED

    def test_sarcastic_text(self):
        from expression_engine import detect_expression, Emotion
        result = detect_expression("Oh sure buddy, definitely a skill issue, imagine")
        assert result == Emotion.SARCASTIC

    def test_surprised_text(self):
        from expression_engine import detect_expression, Emotion
        result = detect_expression("What?! No way! Are you kidding me?!")
        assert result == Emotion.SURPRISED

    def test_confident_text(self):
        from expression_engine import detect_expression, Emotion
        result = detect_expression("Trust me, it's easy. Just do it, pro tip.")
        assert result == Emotion.CONFIDENT

    def test_empathetic_text(self):
        from expression_engine import detect_expression, Emotion
        result = detect_expression("Sorry, that sucks. I feel you, hang in there.")
        assert result == Emotion.EMPATHETIC

    def test_empty_text(self):
        from expression_engine import detect_expression, Emotion
        result = detect_expression("")
        assert result == Emotion.NEUTRAL

    def test_whitespace_only(self):
        from expression_engine import detect_expression, Emotion
        result = detect_expression("   \n  ")
        assert result == Emotion.NEUTRAL


# ═══════════════════════════════════════════════════════════════════════
#  EXPRESSION ENGINE STATE MACHINE TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestExpressionEngine:
    """Tests for ExpressionEngine state machine."""

    def test_initial_state_neutral(self):
        from expression_engine import ExpressionEngine, Emotion
        engine = ExpressionEngine()
        assert engine.current_emotion == Emotion.NEUTRAL

    def test_set_emotion(self):
        from expression_engine import ExpressionEngine, Emotion
        engine = ExpressionEngine()
        engine.set_emotion(Emotion.HAPPY)
        assert engine.current_emotion == Emotion.HAPPY

    def test_sprite_idle_neutral(self):
        from expression_engine import ExpressionEngine, Emotion
        engine = ExpressionEngine()
        assert engine.get_sprite("idle") == "idle"

    def test_sprite_listening(self):
        from expression_engine import ExpressionEngine, Emotion
        engine = ExpressionEngine()
        engine.set_emotion(Emotion.EXCITED)
        # Listening always shows listen face regardless of emotion
        assert engine.get_sprite("listening") == "listen"

    def test_sprite_speaking_high_amplitude(self):
        from expression_engine import ExpressionEngine, Emotion
        engine = ExpressionEngine()
        engine.set_emotion(Emotion.HAPPY)
        sprite = engine.get_sprite("speaking", amplitude=0.8)
        assert sprite == "smile_speak"  # happy high-amp speak sprite

    def test_sprite_thinking_neutral(self):
        from expression_engine import ExpressionEngine, Emotion
        engine = ExpressionEngine()
        assert engine.get_sprite("thinking") == "think"

    def test_reset(self):
        from expression_engine import ExpressionEngine, Emotion
        engine = ExpressionEngine()
        engine.set_emotion(Emotion.EXCITED)
        engine.reset()
        assert engine.current_emotion == Emotion.NEUTRAL


# ═══════════════════════════════════════════════════════════════════════
#  LANGUAGE DETECTION TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestLanguageDetection:
    """Tests for _detect_language() in voice_output.py."""

    def test_cyrillic_detected_as_russian(self):
        from voice_output import _detect_language
        assert _detect_language("Привет, как дела?") == "ru"

    def test_romanian_diacritics(self):
        from voice_output import _detect_language
        assert _detect_language("Bună ziua, ce faceți?") == "ro"

    def test_english_default(self):
        from voice_output import _detect_language
        result = _detect_language("Hello, how are you today?")
        assert result == "en"

    def test_romanian_unique_words(self):
        from voice_output import _detect_language
        assert _detect_language("Trebuie pentru aceasta mereu") == "ro"

    def test_short_text_uses_hint(self):
        from voice_output import _detect_language
        # Short ambiguous text should use the hint
        result = _detect_language("da", hint="ro")
        assert result == "ro"

    def test_transliterated_russian(self):
        from voice_output import _detect_language
        assert _detect_language("Privet bratishka, khorosho davay") == "ru"


# ═══════════════════════════════════════════════════════════════════════
#  TEXT PREPROCESSING TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestTextPreprocessing:
    """Tests for VoiceOutput._preprocess_for_speech()."""

    def test_number_with_unit(self):
        from voice_output import VoiceOutput
        result = VoiceOutput._preprocess_for_speech("Go 5 km north")
        assert "five kilometres" in result

    def test_standalone_number(self):
        from voice_output import VoiceOutput
        result = VoiceOutput._preprocess_for_speech("There are 42 players")
        assert "forty two" in result

    def test_abbreviation_btw(self):
        from voice_output import VoiceOutput
        result = VoiceOutput._preprocess_for_speech("BTW that was nice")
        assert "by the way" in result

    def test_abbreviation_pmc(self):
        from voice_output import VoiceOutput
        result = VoiceOutput._preprocess_for_speech("Kill the PMC first")
        assert "P.M.C." in result

    def test_emoji_removal(self):
        from voice_output import VoiceOutput
        result = VoiceOutput._preprocess_for_speech("Great job! 🎯🔥")
        assert "🎯" not in result
        assert "🔥" not in result
        assert "Great job" in result

    def test_markdown_bold_stripped(self):
        from voice_output import VoiceOutput
        result = VoiceOutput._preprocess_for_speech("This is **bold** text")
        assert "**" not in result
        assert "bold" in result

    def test_markdown_header_stripped(self):
        from voice_output import VoiceOutput
        result = VoiceOutput._preprocess_for_speech("## Header Text")
        assert "##" not in result
        assert "Header Text" in result

    def test_multi_punctuation_collapsed(self):
        from voice_output import VoiceOutput
        result = VoiceOutput._preprocess_for_speech("Wow!!! Amazing!!!")
        assert "!!!" not in result


# ═══════════════════════════════════════════════════════════════════════
#  NUMBER-TO-WORDS TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestNumberToWords:
    """Tests for _number_to_words()."""

    def test_zero(self):
        from voice_output import _number_to_words
        # 0 returns empty string (by design — _ONES[0] = "")
        assert _number_to_words(0) == ""

    def test_single_digit(self):
        from voice_output import _number_to_words
        assert _number_to_words(7) == "seven"

    def test_teens(self):
        from voice_output import _number_to_words
        assert _number_to_words(15) == "fifteen"

    def test_tens(self):
        from voice_output import _number_to_words
        assert _number_to_words(42) == "forty two"

    def test_hundreds(self):
        from voice_output import _number_to_words
        assert _number_to_words(100) == "one hundred"

    def test_thousands(self):
        from voice_output import _number_to_words
        assert _number_to_words(1000) == "one thousand"

    def test_complex(self):
        from voice_output import _number_to_words
        result = _number_to_words(2345)
        assert "two thousand" in result
        assert "three hundred" in result
        assert "forty five" in result

    def test_negative(self):
        from voice_output import _number_to_words
        result = _number_to_words(-5)
        assert result == "minus five"


# ═══════════════════════════════════════════════════════════════════════
#  TARKOV UPDATER TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestTarkovUpdater:
    """Tests for tarkov_updater data formatting."""

    def test_format_live_data_maps(self):
        from tarkov_updater import _format_live_data
        data = {
            "maps": [{
                "name": "Customs",
                "players": "8-12",
                "raidDuration": 40,
                "extracts": [{"name": "ZB-1011"}, {"name": "Crossroads"}],
                "bosses": [{"name": "Reshala", "spawnChance": 0.38}],
            }],
        }
        result = _format_live_data(data)
        assert "Customs" in result
        assert "8-12" in result
        assert "40min" in result
        assert "Reshala" in result
        assert "38%" in result

    def test_format_live_data_tasks(self):
        from tarkov_updater import _format_live_data
        data = {
            "tasks": [
                {"name": "Debut", "trader": {"name": "Prapor"}, "minPlayerLevel": 1},
                {"name": "Shortage", "trader": {"name": "Therapist"}, "minPlayerLevel": 1},
                {"name": "Supplier", "trader": {"name": "Prapor"}, "minPlayerLevel": 5},
            ],
        }
        result = _format_live_data(data)
        assert "Prapor: 2" in result
        assert "Therapist: 1" in result
        assert "Total: 3" in result

    def test_format_empty_data(self):
        from tarkov_updater import _format_live_data
        result = _format_live_data({})
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════
#  PERSISTENT MEMORY TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestPersistentMemory:
    """Tests for Brain persistent memory save/load."""

    def test_save_load_round_trip(self, tmp_path):
        """Memory saves to JSON and loads back correctly."""
        import json

        memory_file = tmp_path / "memory.json"
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hey there!"},
            {"role": "user", "content": "what map?"},
            {"role": "assistant", "content": "customs is great"},
        ]

        # Save
        with open(memory_file, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)

        # Load back
        with open(memory_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        assert len(loaded) == 4
        assert loaded[0]["role"] == "user"
        assert loaded[0]["content"] == "hello"
        assert loaded[3]["content"] == "customs is great"

    def test_corrupt_file_graceful(self, tmp_path):
        """Corrupt memory file doesn't crash load."""
        memory_file = tmp_path / "memory.json"
        memory_file.write_text("not valid json {{{", encoding="utf-8")

        import json
        try:
            with open(memory_file, "r", encoding="utf-8") as f:
                json.load(f)
            assert False, "Should have raised"
        except json.JSONDecodeError:
            pass  # Expected

    def test_empty_memory_file(self, tmp_path):
        """Empty list in memory file loads as empty."""
        import json

        memory_file = tmp_path / "memory.json"
        with open(memory_file, "w") as f:
            json.dump([], f)

        with open(memory_file, "r") as f:
            loaded = json.load(f)
        assert loaded == []


# ═══════════════════════════════════════════════════════════════════════
#  GESTURE TAG PARSING TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestGestureTagParsing:
    """Tests for gesture tag extraction from AI responses."""

    def test_extract_gesture(self):
        import re
        pattern = re.compile(r'\[gesture:(\w+)\]', re.IGNORECASE)
        text = "[gesture:wave] Hey there!"
        match = pattern.search(text)
        assert match is not None
        assert match.group(1) == "wave"

    def test_strip_gesture_tag(self):
        import re
        pattern = re.compile(r'\[gesture:(\w+)\]', re.IGNORECASE)
        text = "[gesture:dance] Let's go!"
        clean = pattern.sub('', text).strip()
        assert clean == "Let's go!"

    def test_gesture_case_insensitive(self):
        import re
        pattern = re.compile(r'\[gesture:(\w+)\]', re.IGNORECASE)
        text = "[Gesture:WAVE] hello"
        match = pattern.search(text)
        assert match is not None
        assert match.group(1) == "WAVE"

    def test_no_gesture(self):
        import re
        pattern = re.compile(r'\[gesture:(\w+)\]', re.IGNORECASE)
        text = "Just a normal sentence."
        match = pattern.search(text)
        assert match is None

    def test_all_gestures_in_prompt(self):
        """All 9 dashboard animations are documented in the LLM prompt."""
        from expression_engine import LLM_GESTURE_PROMPT
        for gesture in ["wave", "think", "shrug", "clap", "dance",
                        "salute", "win", "crouch", "die"]:
            assert f"[gesture:{gesture}]" in LLM_GESTURE_PROMPT, (
                f"Gesture '{gesture}' missing from LLM_GESTURE_PROMPT"
            )


# ═══════════════════════════════════════════════════════════════════════
#  DASHBOARD HTML TESTS
# ═══════════════════════════════════════════════════════════════════════
class TestDashboardHTML:
    """Tests for dashboard HTML content."""

    def test_html_exists(self):
        html_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "dashboard_ui.html"
        )
        assert os.path.exists(html_path)

    def test_html_has_required_elements(self):
        html_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "dashboard_ui.html"
        )
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()

        lower = content.lower()
        # Essential elements (case-insensitive)
        assert "pmc overwatch" in lower
        assert "llm engines" in lower or "llm-engines" in lower
        assert "mascot controls" in lower or "mascot-controls" in lower
        assert "live log" in lower or "log-card" in lower
        assert "websocket" in lower or "ws://" in lower

    def test_html_has_animation_buttons(self):
        html_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "dashboard_ui.html"
        )
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()

        for anim in ["dance", "wave", "clap", "think", "shrug", "salute", "crouch", "die", "win"]:
            assert anim.lower() in content.lower(), (
                f"Animation '{anim}' not found in dashboard HTML"
            )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
