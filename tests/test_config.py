from pathlib import Path

import pytest

from config import Config, load_config, parse_env_file, update_env_file


ENV_CONTENT = """\
IKARIAM_USERNAME=avirzayev@gmail.com
IKARIAM_PASSWORD=aviavi123
IKARIAM_SERVER=s401-en.ikariam.gameforge.com
IKARIAM_CITY_NAME=ClaudeEmpire

NOTION_TOKEN=ntn_testtoken
NOTION_ROOT_PAGE_ID=3dfe84e3-20f5-81b4-9b83-c51021ac8986
"""


def test_parse_env_file_ignores_blank_lines_and_comments(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("# comment\n\nFOO=bar\n")
    assert parse_env_file(env_path) == {"FOO": "bar"}


def test_load_config_reads_required_fields(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(ENV_CONTENT)
    config = load_config(env_path)
    assert config == Config(
        ikariam_username="avirzayev@gmail.com",
        ikariam_password="aviavi123",
        ikariam_server="s401-en.ikariam.gameforge.com",
        ikariam_city_name="ClaudeEmpire",
        notion_token="ntn_testtoken",
        notion_root_page_id="3dfe84e3-20f5-81b4-9b83-c51021ac8986",
    )


def test_load_config_missing_required_key_raises(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("IKARIAM_USERNAME=x\n")
    with pytest.raises(KeyError):
        load_config(env_path)


def test_update_env_file_replaces_existing_key(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=old\nBAR=keep\n")
    update_env_file(env_path, {"FOO": "new"})
    assert env_path.read_text() == "FOO=new\nBAR=keep\n"


def test_update_env_file_appends_missing_key(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=old\n")
    update_env_file(env_path, {"BAZ": "added"})
    assert env_path.read_text() == "FOO=old\nBAZ=added\n"
