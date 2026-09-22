import io
import sys
from pathlib import Path
import pytest

from main import CONSOLE_MAX_CHARS, main, sanitize_message, use_safe_console_encoding
from mock_llm import MockLLM
import state as state_module


def test_sanitize_message_redacts_keys():
    msg = "Error connecting with key gsk_1234567890abcdef and AIzaSyD12345"
    sanitized = sanitize_message(msg)
    assert "gsk_1234567890abcdef" not in sanitized
    assert "AIzaSyD12345" not in sanitized
    assert "[REDACTED_KEY]" in sanitized


def test_sanitize_message_redacts_firecrawl_and_gemini_keys():
    msg = "Firecrawl error 401: invalid key fc-1a2b3c4d5e6f and AQ.Ab8RN6Lsecrettoken"
    sanitized = sanitize_message(msg)
    assert "fc-1a2b3c4d5e6f" not in sanitized
    assert "AQ.Ab8RN6Lsecrettoken" not in sanitized
    assert "[REDACTED_KEY]" in sanitized


def test_safe_console_encoding_uses_replace_and_tolerates_odd_streams(monkeypatch):
    class Recording:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    recording = Recording()
    monkeypatch.setattr(sys, "stdout", recording)
    monkeypatch.setattr(sys, "stderr", object())  # no reconfigure attribute at all

    use_safe_console_encoding()  # must not raise

    assert recording.calls == [{"errors": "replace"}]

    class Broken(Recording):
        def reconfigure(self, **kwargs):
            raise ValueError("stream is not reconfigurable")

    monkeypatch.setattr(sys, "stdout", Broken())
    use_safe_console_encoding()  # still must not raise


def test_long_output_never_printed_in_full_to_console(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    # Create a mock that returns a very long final output
    long_final = "X" * (CONSOLE_MAX_CHARS + 500)

    class LongOutputMock(MockLLM):
        def __call__(self, role: str, system: str, user: str, json_mode: bool = False):
            if role == "tester" and self.tester_call_count >= 1:
                self.tester_call_count += 1
                return long_final
            return super().__call__(role, system, user, json_mode=json_mode)

    monkeypatch.setattr("main.MockLLM", LongOutputMock)
    monkeypatch.setattr("sys.argv", ["main.py", "--mock", "test long output"])

    main()

    captured = capsys.readouterr()
    # The full long string of 1100 Xs must not appear in console stdout
    assert long_final not in captured.out
    assert "Output truncated for console display" in captured.out
    assert f"{len(long_final)} chars total" in captured.out
