from __future__ import annotations

from pathlib import Path

from assistant_tools.providers.groq import upload_file_metadata


def test_upload_file_metadata_aliases_telegram_oga_to_ogg() -> None:
    upload_name: str
    content_type: str
    upload_name, content_type = upload_file_metadata(Path("voice_2026-05-08_13-03-03.oga"))

    assert upload_name == "voice_2026-05-08_13-03-03.ogg"
    assert content_type == "audio/ogg"


def test_upload_file_metadata_keeps_supported_extension() -> None:
    upload_name: str
    content_type: str
    upload_name, content_type = upload_file_metadata(Path("german_2011051.ogg"))

    assert upload_name == "german_2011051.ogg"
    assert content_type == "audio/ogg"
