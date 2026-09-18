from pathlib import Path


class SessionError(Exception):
    pass


def read_cookie(session_dir: Path) -> str:
    cookie_path = session_dir / "cookie.txt"
    if not cookie_path.exists():
        raise SessionError(
            "No session cookie found — refresh it manually (see DESIGN.md §7)."
        )
    cookie = cookie_path.read_text().strip()
    if not cookie:
        raise SessionError(
            "Session cookie file is empty — refresh it manually (see DESIGN.md §7)."
        )
    return cookie


def read_action_request(session_dir: Path) -> str:
    token_path = session_dir / "action_request.txt"
    if not token_path.exists():
        raise SessionError(
            "No actionRequest token found — seed it manually (see DESIGN.md §7)."
        )
    token = token_path.read_text().strip()
    if not token:
        raise SessionError(
            "actionRequest token file is empty — seed it manually (see DESIGN.md §7)."
        )
    return token


def write_action_request(session_dir: Path, token: str) -> None:
    (session_dir / "action_request.txt").write_text(token)
