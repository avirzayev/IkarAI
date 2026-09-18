import pytest

from session_store import SessionError, read_action_request, read_cookie, write_action_request


def test_read_cookie_raises_when_file_missing(tmp_path):
    with pytest.raises(SessionError):
        read_cookie(tmp_path)


def test_read_cookie_raises_when_file_empty(tmp_path):
    (tmp_path / "cookie.txt").write_text("  \n")
    with pytest.raises(SessionError):
        read_cookie(tmp_path)


def test_read_cookie_returns_stripped_content(tmp_path):
    (tmp_path / "cookie.txt").write_text("ikariam_session=abc123\n")
    assert read_cookie(tmp_path) == "ikariam_session=abc123"


def test_read_action_request_raises_when_missing(tmp_path):
    with pytest.raises(SessionError):
        read_action_request(tmp_path)


def test_write_then_read_action_request_roundtrips(tmp_path):
    write_action_request(tmp_path, "token123")
    assert read_action_request(tmp_path) == "token123"
