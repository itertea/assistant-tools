from __future__ import annotations

import textwrap
from pathlib import Path

from assistant_tools.config import load_config


def test_load_config_accepts_tts_backend_field(tmp_path: Path) -> None:
    config_path: Path = tmp_path / "config.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [tts]
            backend = "kittentts"
            voice = "Kiki"
            """
        ).strip()
        + "\n"
    )

    config = load_config(config_path)
    assert config.tts.backend == "kittentts"
    assert config.tts.voice == "Kiki"
