import pytest

from yomikae import detect
from yomikae.config import Settings
from yomikae.detect import drop_merged_blocks
from yomikae.model import Box
from yomikae.ocr import is_japanese_text, looks_like_sfx
from yomikae.translate import parse_translations


def test_parses_structured_and_fenced_output():
    assert parse_translations('{"translations": [{"id": 1, "text": "b"}, {"id": 0, "text": "a"}]}', 2) == ["a", "b"]
    fenced = 'Sure!\n```json\n{"lines": [{"id": 0, "text": "Hi"}]}\n```'
    assert parse_translations(fenced.replace('"lines"', '"translations"'), 1) == ["Hi"]
    assert parse_translations('["x", "y"]', 2) == ["x", "y"]


def test_rejects_misaligned_output():
    assert parse_translations('{"translations": [{"id": 0, "text": "a"}]}', 2) is None
    assert parse_translations('{"translations": [{"id": 0, "text": ""}]}', 1) is None
    assert parse_translations("I can't help with that.", 1) is None


def test_classifies_non_text_sfx_and_dialogue():
    for text in ["", "…", "！？", "ー", "OK♡", "ＲＯＳＴＯｎｉｔｈｅＩＮＴでＥＲＮＥＴ"]:
        assert not is_japanese_text(text), text
    for text in ["ドン", "パシャッ", "ドキドキッ", "あっ", "ふう～～", "けど", "は？", "モデル？"]:
        assert is_japanese_text(text) and looks_like_sfx(text), text
    for text in ["そんで", "船に飾ってもいいかい？", "ミューズだ", "五条さんっ！", "素晴らしいよ…Ｍｒ．ゴジョー"]:
        assert is_japanese_text(text) and not looks_like_sfx(text), text


def test_a_block_around_two_detected_lines_is_dropped_for_the_lines():
    parent = Box(329, 647, 579, 993)
    lines = [Box(539, 666, 580, 968), Box(345, 653, 433, 993)]
    other = Box(169, 652, 258, 948)
    assert drop_merged_blocks([lines[0], parent, lines[1], other]) == [lines[0], lines[1], other]


def test_a_block_holding_one_smaller_block_is_kept():
    boxes = [Box(0, 0, 100, 300), Box(10, 10, 60, 100)]
    assert drop_merged_blocks(boxes) == boxes


def test_overlapping_blocks_are_read_once():
    first, second, apart = Box(917, 166, 987, 481), Box(913, 318, 1018, 604), Box(100, 100, 150, 200)
    assert detect.merge_overlapping([first, second, apart]) == [Box(913, 166, 1018, 604), apart]


def test_device_settings_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("YOMIKAE_DETECT_DEVICE", raising=False)
    monkeypatch.delenv("YOMIKAE_LOW_VRAM", raising=False)
    s = Settings.from_env()
    assert (s.detect_device, s.low_vram) == ("cuda", False)
    monkeypatch.setenv("YOMIKAE_DETECT_DEVICE", "cpu")
    monkeypatch.setenv("YOMIKAE_LOW_VRAM", "1")
    s = Settings.from_env()
    assert (s.detect_device, s.low_vram) == ("cpu", True)
    monkeypatch.setenv("YOMIKAE_DETECT_DEVICE", "gpu")
    with pytest.raises(ValueError, match="YOMIKAE_DETECT_DEVICE"):
        Settings.from_env()
