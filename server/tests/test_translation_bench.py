import asyncio
import json
import subprocess
import sys
import types
from argparse import Namespace

import pytest

import translation_bench

LINE = {"series": "a", "page_index": 1, "line_index": 0, "ja": "行くぞ", "hyp": "Let's go", "ref": "Let's go!"}


def fake_comet(monkeypatch, scored):
    calls = []

    def fake_run(cmd, env, check):
        calls.append((cmd, env))
        run_dir = sys.modules["pathlib"].Path(cmd[cmd.index("--out-root") + 1]) / cmd[cmd.index("--label") + 1]
        todo = json.loads((run_dir / "comet-todo.json").read_text())
        scored.extend(todo)
        (run_dir / "comet-done.json").write_text(json.dumps([0.5] * len(todo)))

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def comet_args(device="cpu", fresh=False):
    return Namespace(comet_model="m", comet_python=sys.executable, comet_device=device, comet_fresh=fresh)


def run_comet_scores(monkeypatch, tmp_path, device):
    calls = fake_comet(monkeypatch, [])
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    (tmp_path / "run").mkdir()
    assert translation_bench.comet_scores(tmp_path / "run", comet_args(device), [LINE]) == ({"a/1/0": 0.5}, 0)
    return calls[0]


def test_comet_on_cpu_hides_the_gpu(monkeypatch, tmp_path):
    cmd, env = run_comet_scores(monkeypatch, tmp_path, "cpu")
    assert env["CUDA_VISIBLE_DEVICES"] == ""
    assert cmd[cmd.index("--comet-device") + 1] == "cpu"


def test_comet_on_auto_keeps_the_gpu_visible_and_passes_the_device(monkeypatch, tmp_path):
    cmd, env = run_comet_scores(monkeypatch, tmp_path, "auto")
    assert env["CUDA_VISIBLE_DEVICES"] == "0"
    assert cmd[cmd.index("--comet-device") + 1] == "auto"


def fake_torch(available, free):
    cuda = types.SimpleNamespace(is_available=lambda: available, mem_get_info=lambda: (free, 16 * 1024**3))
    return types.SimpleNamespace(cuda=cuda, version=types.SimpleNamespace(cuda="12.4"))


@pytest.mark.parametrize(
    ("device", "available", "free", "gpus"),
    [
        ("cpu", True, 16 * 1024**3, 0),
        ("auto", False, 0, 0),
        ("auto", True, 2 * 1024**3, 0),
        ("auto", True, 8 * 1024**3, 1),
    ],
)
def test_comet_auto_uses_the_gpu_only_with_room(monkeypatch, device, available, free, gpus):
    monkeypatch.setitem(sys.modules, "torch", fake_torch(available, free))
    assert translation_bench.comet_gpus(device) == gpus


def earlier_run(root, name, model, rows):
    (root / name).mkdir(parents=True)
    (root / name / "summary.json").write_text(json.dumps({"comet_model": model}))
    (root / name / "lines.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def test_comet_scores_only_lines_no_earlier_run_scored(monkeypatch, tmp_path):
    other = {**LINE, "line_index": 1, "hyp": "Go on"}
    earlier_run(tmp_path, "old", "m", [{**LINE, "comet": 0.9}])
    earlier_run(tmp_path, "other-model", "n", [{**other, "comet": 0.1}])
    scored = []
    fake_comet(monkeypatch, scored)
    (tmp_path / "run").mkdir()
    scores, reused = translation_bench.comet_scores(tmp_path / "run", comet_args(), [LINE, other])
    assert scores == {"a/1/0": 0.9, "a/1/1": 0.5}
    assert reused == 1
    assert scored == [["行くぞ", "Go on", "Let's go!"]]


def test_comet_fresh_scores_every_line_and_a_changed_line_is_scored_again(monkeypatch, tmp_path):
    earlier_run(tmp_path, "old", "m", [{**LINE, "comet": 0.9}])
    scored = []
    fake_comet(monkeypatch, scored)
    (tmp_path / "run").mkdir()
    assert translation_bench.comet_scores(tmp_path / "run", comet_args(fresh=True), [LINE]) == ({"a/1/0": 0.5}, 0)
    assert translation_bench.comet_scores(tmp_path / "run", comet_args(fresh=True), [LINE]) == ({"a/1/0": 0.5}, 0)
    assert len(scored) == 1
    translation_bench.comet_scores(tmp_path / "run", comet_args(fresh=True), [{**LINE, "hyp": "Move"}])
    assert scored[-1] == ["行くぞ", "Move", "Let's go!"]


ROMAJI = {"北": "kita", "南": "minami", "東": "higashi"}


class FakeTranslator:
    in_flight = 0
    most = 0
    calls: list[str] = []

    def __init__(self, settings):
        self.name = "fake-slow"
        self.prompt_tokens = 0

    async def health(self):
        return None

    async def translate(self, lines, context):
        cls = FakeTranslator
        cls.in_flight += 1
        cls.most = max(cls.most, cls.in_flight)
        cls.calls.append(lines[0])
        await asyncio.sleep(0.01 * (len(cls.calls) % 3 + 1))
        cls.in_flight -= 1
        self.prompt_tokens += 10
        return [f"{ROMAJI[line[0]]} {line[2]} after {context.pages[-1][-1].translation if context.pages else 'nothing'}" for line in lines]


def annotation(tmp_path):
    books = [
        {"book_title": series, "pages": [{"page_index": i, "image_paths": {"ja": "none.png"}, "text": [{"text_ja": f"{series}の{i}ページ", "text_en": "ref", "x": 0, "y": 0, "w": 10, "h": 10}]} for i in range(pages)]}
        for series, pages in (("北", 5), ("南", 3), ("東", 4))
    ]
    path = tmp_path / "annotation.json"
    path.write_text(json.dumps(books, ensure_ascii=False))
    return path


def bench(monkeypatch, tmp_path, label, *extra):
    monkeypatch.setattr("yomikae.translate.make_translator", FakeTranslator)
    FakeTranslator.in_flight, FakeTranslator.most, FakeTranslator.calls = 0, 0, []
    args = translation_bench.parser().parse_args([str(annotation(tmp_path)), "--out-root", str(tmp_path / "runs"), "--label", label, *extra])
    asyncio.run(translation_bench.run(args))
    pages = {f.name: json.loads(f.read_text()) for f in sorted((tmp_path / "runs" / label / "pages").glob("*.json"))}
    return {name: {k: v for k, v in page.items() if k != "ms"} for name, page in pages.items()}


def test_concurrent_series_keep_reading_order_and_match_a_serial_run(monkeypatch, tmp_path):
    serial = bench(monkeypatch, tmp_path, "serial", "--jobs", "1")
    assert FakeTranslator.most == 1
    together = bench(monkeypatch, tmp_path, "together", "--jobs", "3")
    assert FakeTranslator.most == 3
    for series in ("北", "南", "東"):
        assert [c for c in FakeTranslator.calls if c.startswith(series)] == [f"{series}の{i}ページ" for i in range(len([c for c in FakeTranslator.calls if c.startswith(series)]))]
    assert FakeTranslator.calls != sorted(FakeTranslator.calls, key=lambda c: "北南東".index(c[0]))
    assert together == serial
    assert len(serial) == 12
    assert serial["北__004.json"]["lines"][0]["hyp"] == "kita 4 after kita 3 after kita 2 after kita 1 after kita 0 after nothing"
    assert serial["北__004.json"]["prompt_tokens"] == 10


def test_reuse_copies_a_finished_run_with_the_same_fingerprint(monkeypatch, tmp_path):
    monkeypatch.setattr(translation_bench, "git_sha", lambda path: "abc1234")
    monkeypatch.setattr(translation_bench, "git_dirty", lambda path: False)
    first = bench(monkeypatch, tmp_path, "first")
    again = bench(monkeypatch, tmp_path, "again", "--reuse")
    assert FakeTranslator.calls == []
    assert again == first
    meta = json.loads((tmp_path / "runs" / "again" / "meta.json").read_text())
    assert meta["reused_from"] == "first"
    bench(monkeypatch, tmp_path, "subset", "--reuse", "--pages", "0,2")
    assert len(FakeTranslator.calls) == 6


def test_reuse_never_takes_a_run_from_uncommitted_code(monkeypatch, tmp_path):
    monkeypatch.setattr(translation_bench, "git_sha", lambda path: "abc1234")
    monkeypatch.setattr(translation_bench, "git_dirty", lambda path: True)
    bench(monkeypatch, tmp_path, "first")
    bench(monkeypatch, tmp_path, "again", "--reuse")
    assert len(FakeTranslator.calls) == 12


def test_page_list_picks_pages_in_every_series_or_one():
    pages = [{"series": s, "page_index": i} for s in ("a", "b") for i in range(5)]
    assert [(p["series"], p["page_index"]) for p in translation_bench.pick_pages(pages, {}, "1,3", [])] == [("a", 1), ("a", 3), ("b", 1), ("b", 3)]
    assert [(p["series"], p["page_index"]) for p in translation_bench.pick_pages(pages, {}, "b:2,4", [])] == [("a", 4), ("b", 2), ("b", 4)]


def test_openers_are_the_first_three_story_pages_of_each_series_and_chapter():
    pages = [{"series": s, "page_index": i} for s in ("a", "b") for i in range(12)]
    filler = {("a", 0), ("a", 2), ("b", 0), ("b", 6)}
    ahead = {s: [{"page_index": i, "filler": (s, i) in filler} for i in range(12)] for s in ("a", "b")}
    picked = translation_bench.pick_pages(pages, ahead, "openers", [])
    assert [(p["series"], p["page_index"]) for p in picked] == [("a", 1), ("a", 3), ("a", 4), ("b", 1), ("b", 2), ("b", 3)]
    chapters = translation_bench.pick_pages([p for p in pages if p["series"] == "b"], {"b": ahead["b"]}, "openers", [0, 6])
    assert [p["page_index"] for p in chapters] == [1, 2, 3, 7, 8, 9]
