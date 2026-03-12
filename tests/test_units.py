"""
Unit tests for PMC Overwatch — pure-function logic.

Run with:
    cd c:\\Users\\maria\\.gemini\\antigravity\\scratch\\Tarkov_AI_Frriend
    .\\venv\\Scripts\\python.exe -m pytest tests/test_units.py -v

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


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
