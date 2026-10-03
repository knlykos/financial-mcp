import sys

from .simplefin import ACCESS_URL_FILE, SimpleFINError, claim_setup_token, save_access_url


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: uv run pfm-setup <simplefin-setup-token>")
    token = sys.argv[1]
    try:
        # Accept an access URL directly too, in case the token was already claimed elsewhere.
        access_url = token if token.startswith("https://") else claim_setup_token(token)
    except SimpleFINError as e:
        sys.exit(str(e))
    save_access_url(access_url)
    print(f"Access URL saved to {ACCESS_URL_FILE}")
