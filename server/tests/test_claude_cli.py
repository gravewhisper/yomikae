from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from yomikae import translate
from yomikae.config import Settings
from yomikae.model import ContextLine
from yomikae.translate import Context, Misaligned, TranslatorFailed, TranslatorUnavailable, make_translator

FAKE_CLI = """
import json, os, sys
from pathlib import Path
out = Path(os.environ["FAKE_CLAUDE_DIR"])
(out / "argv.json").write_text(json.dumps(sys.argv[1:]))
(out / "stdin.txt").write_text(sys.stdin.read())
(out / "cwd.txt").write_text(os.getcwd())
(out / "cwd_files.json").write_text(json.dumps(os.listdir()))
sys.stdout.write((out / "reply.txt").read_text())
sys.exit(int((out / "exit.txt").read_text()) if (out / "exit.txt").exists() else 0)
"""


def result_event(**fields) -> str:
    return json.dumps([{"type": "system", "subtype": "init"}, {"type": "result", "subtype": "success", "is_error": False, **fields}])


@pytest.fixture
def cli(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text(f"#!{sys.executable}\n{FAKE_CLI}")
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_CLAUDE_DIR", str(tmp_path))
    translator = make_translator(replace(Settings.from_env(), translator="claude", claude_command=str(script), claude_model="claude-haiku-5-5", claude_effort="low"))
    return translator, tmp_path


def test_the_claude_translator_reads_the_structured_reply(cli):
    translator, out = cli
    (out / "reply.txt").write_text(result_event(structured_output={"translations": ["Morning, Tanaka-san", "Let's go!"]}, result="ignored"))
    context = Context(pages=[[ContextLine("田中さん、起きて", "Tanaka-san, wake up")]])
    assert asyncio.run(translator.translate(["おはよう、田中さん", "行こう！"], context)) == ["Morning, Tanaka-san", "Let's go!"]
    assert translator.name == "claude:claude-haiku-5-5"
    sent = json.loads((out / "stdin.txt").read_text())
    assert [line["text"] for line in sent["lines"]] == ["おはよう、田中さん", "行こう！"]
    assert sent["previous_pages"] == [[{"ja": "田中さん、起きて", "en": "Tanaka-san, wake up"}]]


def test_the_claude_translator_runs_with_no_tools_no_settings_and_no_session(cli):
    translator, out = cli
    (out / "reply.txt").write_text(result_event(structured_output={"translations": ["Hi"]}))
    asyncio.run(translator.translate(["やあ"], Context()))
    argv = json.loads((out / "argv.json").read_text())
    assert argv[argv.index("--tools") + 1] == ""
    assert {"-p", "--safe-mode", "--no-session-persistence"} <= set(argv)
    assert argv[argv.index("--model") + 1] == "claude-haiku-5-5"
    assert argv[argv.index("--effort") + 1] == "low"
    assert "translations" in json.loads(argv[argv.index("--json-schema") + 1])["properties"]
    assert "Japanese manga dialogue" in argv[argv.index("--system-prompt") + 1]
    assert Path((out / "cwd.txt").read_text()) != Path.cwd()
    assert json.loads((out / "cwd_files.json").read_text()) == []


def test_a_reply_in_prose_is_misaligned_and_carries_the_text(cli):
    translator, out = cli
    (out / "reply.txt").write_text(result_event(result="I can't help with translating this page."))
    with pytest.raises(Misaligned, match="can't help"):
        asyncio.run(translator.translate(["やあ"], Context()))


def test_a_failed_run_fails_the_page_without_making_the_translator_unavailable(cli):
    translator, out = cli
    (out / "reply.txt").write_text(json.dumps([{"type": "result", "subtype": "error", "is_error": True, "result": "API Error: 500 Internal server error"}]))
    with pytest.raises(TranslatorFailed, match="500"):
        asyncio.run(translator.translate(["やあ"], Context()))
    assert asyncio.run(translator.health()) is None
    (out / "reply.txt").write_text("")
    (out / "exit.txt").write_text("1")
    with pytest.raises(TranslatorFailed):
        asyncio.run(translator.translate(["やあ"], Context()))


def test_health_reports_a_missing_claude_command(cli, tmp_path):
    translator, _ = cli
    assert asyncio.run(translator.health()) is None
    missing = make_translator(replace(Settings.from_env(), translator="claude", claude_command=str(tmp_path / "nope")))
    assert "nope" in asyncio.run(missing.health())
    with pytest.raises(TranslatorUnavailable):
        asyncio.run(missing.translate(["やあ"], Context()))


def test_claude_is_a_registered_translator():
    assert "claude" in translate.REGISTRY


def test_each_call_is_saved_when_a_log_folder_is_set(cli, tmp_path):
    translator, out = cli
    translator.log_dir = tmp_path / "calls"
    (out / "reply.txt").write_text(result_event(structured_output={"translations": ["Hi"]}))
    asyncio.run(translator.translate(["やあ"], Context()))
    (saved,) = (tmp_path / "calls").iterdir()
    entry = json.loads(saved.read_text())
    assert entry["request"]["lines"] == [{"id": 0, "text": "やあ"}]
    assert "Japanese manga dialogue" in entry["system"]
    assert entry["reply"][-1]["structured_output"] == {"translations": ["Hi"]}


def test_claude_is_not_told_to_translate_without_censoring(cli):
    translator, out = cli
    (out / "reply.txt").write_text(result_event(structured_output={"translations": ["Hi"]}))
    asyncio.run(translator.translate(["やあ"], Context()))
    argv = json.loads((out / "argv.json").read_text())
    system = argv[argv.index("--system-prompt") + 1]
    assert "censor" not in system
    assert "Japanese honorifics" in system
    assert "censor" in translate.SYSTEM_PROMPT


@pytest.mark.parametrize("message", ["Not logged in · Please run /login", "Invalid API key · Please run /login", "Claude AI usage limit reached|1763800000", "You've hit your limit · resets 3pm"])
def test_a_signed_out_or_used_up_claude_makes_the_translator_unavailable_for_a_while(cli, monkeypatch, message):
    translator, out = cli
    (out / "reply.txt").write_text(json.dumps([{"type": "result", "subtype": "success", "is_error": True, "result": message}]))
    (out / "exit.txt").write_text("1")
    with pytest.raises(TranslatorUnavailable, match="claude"):
        asyncio.run(translator.translate(["やあ"], Context()))
    assert message[:12] in asyncio.run(translator.health())
    monkeypatch.setattr(translate, "OUTAGE_SECONDS", 0.0)
    (out / "reply.txt").write_text(result_event(structured_output={"translations": ["Hi"]}))
    (out / "exit.txt").unlink()
    asyncio.run(translator.translate(["やあ"], Context()))
    assert asyncio.run(translator.health()) is None
