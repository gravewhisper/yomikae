import json
import subprocess
import sys
import types
from argparse import Namespace

import pytest

import translation_bench


def run_comet_scores(monkeypatch, tmp_path, device):
    calls = []

    def fake_run(cmd, env, check):
        calls.append((cmd, env))
        (tmp_path / "run" / "comet.json").write_text(json.dumps({"model": "m", "scores": {"a/1/0": 0.5}}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    (tmp_path / "run").mkdir()
    args = Namespace(comet_model="m", comet_python=sys.executable, comet_device=device)
    assert translation_bench.comet_scores(tmp_path / "run", args, ["a/1/0"]) == {"a/1/0": 0.5}
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
    return types.SimpleNamespace(cuda=cuda)


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
