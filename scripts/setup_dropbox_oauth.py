"""One-time setup: get a Dropbox refresh token.

Usage:
    python scripts/setup_dropbox_oauth.py <APP_KEY> <APP_SECRET>

Steps:
    1. Script prints a Dropbox auth URL — open it in a browser.
    2. Authorize the app for your Dropbox account.
    3. Dropbox shows you an authorization code — paste it back into the
       terminal.
    4. Script exchanges the code for a long-lived refresh token and prints
       it. Copy that into .env as DROPBOX_REFRESH_TOKEN.
"""

import sys

from dropbox import DropboxOAuth2FlowNoRedirect


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1

    app_key, app_secret = sys.argv[1], sys.argv[2]

    flow = DropboxOAuth2FlowNoRedirect(
        consumer_key=app_key,
        consumer_secret=app_secret,
        token_access_type="offline",  # required for refresh tokens
    )

    auth_url = flow.start()
    print()
    print("1. Open this URL in your browser:")
    print(f"   {auth_url}")
    print()
    print("2. Click 'Allow' to grant the app access to your Dropbox.")
    print("3. Dropbox will display an authorization code. Copy it.")
    print()
    auth_code = input("Paste the authorization code here: ").strip()

    try:
        result = flow.finish(auth_code)
    except Exception as exc:
        print(f"\nError exchanging code: {exc}", file=sys.stderr)
        return 2

    print()
    print("Success. Save this refresh token to your .env as DROPBOX_REFRESH_TOKEN:")
    print()
    print(f"DROPBOX_REFRESH_TOKEN={result.refresh_token}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
