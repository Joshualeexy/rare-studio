"""
Niche Profile Loader & Validator.
"""

from pathlib import Path
from typing import Any, Dict
import toml


def load_profile(profile_path: str) -> Dict[str, Any]:
    path = Path(profile_path)
    if not path.exists():
        # Check inside profiles/ directory as fallback
        alt_path = Path("profiles") / f"{profile_path}.toml"
        if alt_path.exists():
            path = alt_path
        else:
            raise FileNotFoundError(f"Profile '{profile_path}' not found at {path} or {alt_path}")

    data = toml.loads(path.read_text(encoding="utf-8"))
    
    # Defaults & Normalization
    niche = data.setdefault("niche", {})
    niche.setdefault("name", path.stem.replace("_", " ").title())
    niche.setdefault("slug", path.stem)
    niche.setdefault("language", "en")

    llm_cfg = data.setdefault("llm", {})
    llm_cfg.setdefault("provider", "ollama")
    llm_cfg.setdefault("model", "qwen3-coder-agent:latest")

    voice = data.setdefault("voice", {})
    voice.setdefault("provider", "edge")
    voice.setdefault("voice_name", "en-US-ChristopherNeural")
    voice.setdefault("voice_rate", 1.0)
    voice.setdefault("voice_volume", 1.0)

    visual = data.setdefault("visual", {})
    visual.setdefault("aspect_ratio", "9:16")
    visual.setdefault("source", "pexels")
    visual.setdefault("clip_duration", 3.0)
    visual.setdefault("transition_mode", "fade")
    visual.setdefault("concat_mode", "sequential")

    subtitle = data.setdefault("subtitle", {})
    subtitle.setdefault("enabled", True)
    subtitle.setdefault("position", "custom")
    subtitle.setdefault("custom_position", 70.0)

    audio = data.setdefault("audio", {})
    audio.setdefault("bgm_type", "random")
    audio.setdefault("bgm_volume", 0.18)

    series = data.setdefault("series", {})
    series.setdefault("enabled", False)
    series.setdefault("current_arc", "")
    series.setdefault("current_episode", 1)

    return data
