from urllib.parse import urlsplit

ACCOUNT_PATH = "/auth/account"


def get_safe_redirect_target(next_url: str | None) -> str:
    if not next_url:
        return ACCOUNT_PATH

    parsed = urlsplit(next_url)
    if parsed.scheme or parsed.netloc:
        return ACCOUNT_PATH
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return ACCOUNT_PATH
    return next_url
