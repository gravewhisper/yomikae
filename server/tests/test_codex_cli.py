from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace

import pytest

from yomikae import translate
from yomikae.config import Settings
from yomikae.translate import Context, Misaligned, TranslatorFailed, TranslatorUnavailable, make_translator

FAKE_CLI = """
import json, os, sys
from pathlib import Path
out = Path(os.environ["FAKE_CODEX_DIR"])
args = sys.argv[1:]
(out / "argv.json").write_text(json.dumps(args))
(out / "stdin.txt").write_text(sys.stdin.read())
(out / "cwd_files.json").write_text(json.dumps(sorted(os.listdir(args[args.index("-C") + 1]))))
for i, a in enumerate(args):
    if a == "-c" and args[i + 1].startswith("model_instructions_file="):
        (out / "instructions.txt").write_text(Path(json.loads(args[i + 1].split("=", 1)[1])).read_text())
(out / "schema.json").write_text(Path(args[args.index("--output-schema") + 1]).read_text())
reply = out / "reply.txt"
if reply.exists():
    Path(args[args.index("-o") + 1]).write_text(reply.read_text())
sys.stderr.write((out / "stderr.txt").read_text() if (out / "stderr.txt").exists() else "")
sys.exit(int((out / "exit.txt").read_text()) if (out / "exit.txt").exists() else 0)
"""


@pytest.fixture
def cli(tmp_path, monkeypatch):
    script = tmp_path / "codex"
    script.write_text(f"#!{sys.executable}\n{FAKE_CLI}")
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_CODEX_DIR", str(tmp_path))
    translator = make_translator(replace(Settings.from_env(), translator="codex", codex_command=str(script), codex_model="", codex_effort="none"))
    return translator, tmp_path


def test_the_codex_translator_reads_the_last_message(cli):
    translator, out = cli
    (out / "reply.txt").write_text('{"translations": ["Morning, Tanaka-san", "Let\'s go!"]}')
    assert asyncio.run(translator.translate(["おはよう、田中さん", "行こう！"], Context())) == ["Morning, Tanaka-san", "Let's go!"]
    assert translator.name == "codex:default"
    sent = json.loads((out / "stdin.txt").read_text())
    assert [line["text"] for line in sent["lines"]] == ["おはよう、田中さん", "行こう！"]


def test_the_codex_translator_runs_with_no_shell_its_own_instructions_and_a_strict_schema(cli):
    translator, out = cli
    (out / "reply.txt").write_text('{"translations": ["Hi"]}')
    asyncio.run(translator.translate(["やあ"], Context()))
    argv = json.loads((out / "argv.json").read_text())
    assert argv[0] == "exec"
    assert {"--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"} <= set(argv)
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "shell_tool" in [argv[i + 1] for i, a in enumerate(argv) if a == "--disable"]
    assert 'model_reasoning_effort="none"' in argv
    assert "-m" not in argv
    instructions = (out / "instructions.txt").read_text()
    assert "Japanese manga dialogue" in instructions and "censor" not in instructions
    assert json.loads((out / "schema.json").read_text())["additionalProperties"] is False
    assert json.loads((out / "cwd_files.json").read_text()) == ["instructions.md", "schema.json"]


def test_a_named_model_is_passed_and_named(cli):
    translator, out = cli
    translator = make_translator(replace(Settings.from_env(), translator="codex", codex_command=translator.command, codex_model="gpt-x"))
    (out / "reply.txt").write_text('{"translations": ["Hi"]}')
    asyncio.run(translator.translate(["やあ"], Context()))
    argv = json.loads((out / "argv.json").read_text())
    assert argv[argv.index("-m") + 1] == "gpt-x"
    assert translator.name == "codex:gpt-x"


def test_a_reply_in_prose_is_misaligned_and_carries_the_text(cli):
    translator, out = cli
    (out / "reply.txt").write_text("Sorry, I can't translate this.")
    with pytest.raises(Misaligned, match="can't translate"):
        asyncio.run(translator.translate(["やあ"], Context()))


def test_a_failed_run_fails_the_page_with_the_error(cli):
    translator, out = cli
    (out / "stderr.txt").write_text("ERROR: You've hit your usage limit")
    (out / "exit.txt").write_text("1")
    with pytest.raises(TranslatorFailed, match="usage limit"):
        asyncio.run(translator.translate(["やあ"], Context()))


def test_a_missing_codex_command_makes_the_translator_unavailable(tmp_path):
    missing = make_translator(replace(Settings.from_env(), translator="codex", codex_command=str(tmp_path / "nope")))
    assert "nope" in asyncio.run(missing.health())
    with pytest.raises(TranslatorUnavailable):
        asyncio.run(missing.translate(["やあ"], Context()))


def test_codex_is_a_registered_translator():
    assert "codex" in translate.REGISTRY
