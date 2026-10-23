"""The connections file is read again when it appears or changes (a fresh `docker compose up` writes it after the service has started)."""
import json

import pytest

from app.credentials import CredentialProvider
from app.errors import IncuBrixError
from app.models import Destination


def destination() -> Destination:
    return Destination(name="Lemmy test", connector_key="lemmy", rules={})


def test_file_written_after_start_is_picked_up(tmp_path):
    path = tmp_path / "connections.json"
    provider = CredentialProvider(str(path), simulation_mode=False, mock_url="http://mock")
    with pytest.raises(IncuBrixError):  # nothing yet: refused, not guessed
        provider.for_destination(destination())
    path.write_text(json.dumps({"lemmy": {"base_url": "http://lemmy:8536", "username": "bot", "password": "pw-1"}}), encoding="utf-8")
    assert provider.for_destination(destination()).base_url == "http://lemmy:8536"


def test_changed_file_is_read_again(tmp_path):
    path = tmp_path / "connections.json"
    path.write_text(json.dumps({"lemmy": {"base_url": "http://one:1"}}), encoding="utf-8")
    provider = CredentialProvider(str(path), simulation_mode=False, mock_url="http://mock")
    assert provider.for_destination(destination()).base_url == "http://one:1"
    path.write_text(json.dumps({"lemmy": {"base_url": "http://two:2222"}}), encoding="utf-8")
    assert provider.for_destination(destination()).base_url == "http://two:2222"
