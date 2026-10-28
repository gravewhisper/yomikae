from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = SERVER_DIR / "models"
FONTS_DIR = SERVER_DIR / "fonts"
FONT_PATH = FONTS_DIR / "ComicNeue-Bold.ttf"
LETTERING_FONTS = {
    "upper": ("ShantellSans-Medium.ttf", "Bangers-Regular.ttf"),
    "mixed": ("ComicNeue-Regular.ttf", "ComicNeue-Bold.ttf"),
}
ANIME_ACE = "animeace2_reg.ttf"
FALLBACK_FONT_PATHS = (SERVER_DIR / "fonts" / "NotoSansSymbols2-Regular.ttf", SERVER_DIR / "fonts" / "NotoSansSymbols-Variable.ttf")
DETECTOR_PATH = MODELS_DIR / "comictextdetector.pt.onnx"
LAMA_PATH = MODELS_DIR / "lama_large_512px.ckpt"
BUBBLE_MODEL_PATH = MODELS_DIR / os.environ.get("YOMIKAE_BUBBLE_MODEL", "manga109-bubble-seg.onnx")
CACHE_MAX_BYTES = 2 * 1024**3
DEVICES = ("cpu", "cuda")
INPUT_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "AVIF": "avif"}


@dataclass(frozen=True)
class Settings:
    cache_dir: Path
    reports_dir: Path
    translator: str
    openai_base_url: str
    openai_model: str
    openai_api_key: str
    openai_reasoning_effort: str
    case: str
    cache_max_bytes: int = CACHE_MAX_BYTES
    detect_device: str = "cuda"
    low_vram: bool = False

    @staticmethod
    def from_env() -> Settings:
        env = os.environ.get
        return Settings(
            cache_dir=Path(env("YOMIKAE_CACHE_DIR", str(SERVER_DIR / "cache"))),
            reports_dir=Path(env("YOMIKAE_REPORTS_DIR") or Path.home() / ".local/share/yomikae/reports"),
            translator=env("YOMIKAE_TRANSLATOR", "openai"),
            openai_base_url=env("YOMIKAE_OPENAI_BASE_URL", "http://localhost:1234/v1").rstrip("/"),
            openai_model=env("YOMIKAE_OPENAI_MODEL", "google/gemma-4-12b"),
            openai_api_key=env("YOMIKAE_OPENAI_API_KEY", "lm-studio"),
            openai_reasoning_effort=env("YOMIKAE_OPENAI_REASONING_EFFORT", "none"),
            case=parse_case(env("YOMIKAE_CASE", "upper")),
            cache_max_bytes=int(env("YOMIKAE_CACHE_MAX_BYTES") or CACHE_MAX_BYTES),
            detect_device=parse_device(env("YOMIKAE_DETECT_DEVICE", "cuda")),
            low_vram=env("YOMIKAE_LOW_VRAM") == "1",
        )


def lettering_fonts(case: str, fonts: Path = FONTS_DIR, env=os.environ) -> tuple[Path, Path]:
    dialogue, shout = (fonts / name for name in LETTERING_FONTS[case])
    if case == "upper" and (fonts / ANIME_ACE).exists():
        dialogue = fonts / ANIME_ACE
    return Path(env.get("YOMIKAE_DIALOGUE_FONT") or dialogue), Path(env.get("YOMIKAE_SHOUT_FONT") or shout)


def extension_ids(env=os.environ) -> tuple[str, ...]:
    return tuple(i.strip() for i in env.get("YOMIKAE_EXTENSION_ID", "").split(",") if i.strip())


def parse_case(value: str) -> str:
    if value not in LETTERING_FONTS:
        raise ValueError(f"YOMIKAE_CASE must be one of {', '.join(LETTERING_FONTS)}, not {value!r}")
    return value


def parse_device(value: str) -> str:
    if value not in DEVICES:
        raise ValueError(f"YOMIKAE_DETECT_DEVICE must be one of {', '.join(DEVICES)}, not {value!r}")
    return value
