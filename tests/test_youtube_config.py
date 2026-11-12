from __future__ import annotations

import pytest

from matrix_ai.config import MatrixConfig
from matrix_ai.errors import ConfigError


def test_youtube_write_scopes_are_opt_in_and_validated(tmp_path):
    config = MatrixConfig(home=tmp_path, additional_scopes=("youtube.write", "youtube.upload"))
    assert set(config.additional_scopes) == {"youtube.write", "youtube.upload"}
    assert config.youtube_token_path == str(config.home / "youtube-token.json")
    assert config.youtube_max_upload_bytes == 2_000_000_000

    with pytest.raises(ConfigError, match="domain.action"):
        MatrixConfig(home=tmp_path / "invalid", additional_scopes=("*",))
