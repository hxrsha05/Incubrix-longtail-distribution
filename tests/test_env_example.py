"""`copy .env.example .env` must give working settings: no setting may swallow a comment, and every setting must be listed."""
import re
import shutil
from pathlib import Path

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def test_no_value_loaded_from_the_example_file_contains_a_comment(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env")
    loaded = Settings(_env_file=tmp_path / ".env")
    for name in Settings.model_fields:
        value = getattr(loaded, name)
        if hasattr(value, "get_secret_value"):
            value = value.get_secret_value()
        assert "#" not in str(value), f"{name} picked up a comment from .env.example: {value!r}"


def test_every_setting_is_listed_in_the_example_file_and_the_readme():
    example = (ROOT / ".env.example").read_text(encoding="utf-8").upper()
    readme = (ROOT / "README.md").read_text(encoding="utf-8").upper()
    names = re.findall(r"^    ([a-z_0-9]+): ", (ROOT / "app" / "config.py").read_text(encoding="utf-8"), re.M)
    assert [n for n in names if n.upper() not in example] == []
    assert [n for n in names if n.upper() not in readme] == []
