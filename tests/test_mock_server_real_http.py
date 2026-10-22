"""Over a REAL connection, so a client timeout really happens (unlike TestClient, which never gives up)."""
import httpx
import pytest

DISCOURSE = {"Api-Key": "mock-discourse-key", "Api-Username": "bot"}
BODY = {"title": "A long enough title", "raw": "A body that is longer than twenty characters."}


def test_a_real_client_times_out_but_the_post_exists(mock):
    """The nasty case the idempotency work is for: the platform created the post, the answer never arrived."""
    mock.fail(platform="discourse", op="create", kind="timeout_after", delay_s=1.0)
    with pytest.raises(httpx.ReadTimeout):
        httpx.post(f"{mock.url}/discourse/posts.json", headers=DISCOURSE, json=BODY, timeout=0.3)
    assert mock.state()["discourse_topics"] == 1


def test_a_real_client_times_out_and_nothing_exists(mock):
    mock.fail(platform="discourse", op="create", kind="timeout_before", delay_s=1.0)
    with pytest.raises(httpx.ReadTimeout):
        httpx.post(f"{mock.url}/discourse/posts.json", headers=DISCOURSE, json=BODY, timeout=0.3)
    assert mock.state()["discourse_topics"] == 0


def test_each_test_starts_with_an_empty_mock(mock):
    assert mock.state()["discourse_topics"] == 0 and mock.state()["rules"] == []
