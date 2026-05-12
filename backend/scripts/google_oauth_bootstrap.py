# google_oauth_bootstrap.py — one-time helper to generate a Google OAuth refresh token for Drive
# Run this locally, complete the browser consent, then copy the refresh token into backend/.env.

import os
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import httpx
from dotenv import load_dotenv

load_dotenv()


def _require_env(name: str) -> str:
    """
    Read a required environment variable.
    Takes: env var name.
    Returns: its value (string). Exits with a clear error if missing.
    """
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def main() -> None:
    """
    Starts a local callback server, opens the Google consent screen, and exchanges the code for tokens.
    Prints the refresh token you should store in backend/.env as DRIVE_OAUTH_REFRESH_TOKEN.
    """
    client_id = _require_env("DRIVE_OAUTH_CLIENT_ID")
    client_secret = _require_env("DRIVE_OAUTH_CLIENT_SECRET")

    # Scopes requested for Drive syncing (read-only + file access).
    scopes = [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.file",
    ]

    # Loopback redirect URI (works for local desktop-style OAuth flows).
    # This must be allowed by your OAuth client configuration in Google Cloud Console.
    host = "127.0.0.1"
    port = 8765
    redirect_uri = f"http://{host}:{port}/callback"

    state = "sentinel_ai_drive_sync"

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        # The two flags below are what make Google return a refresh token.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    # We capture the code from the callback request in memory.
    result = {"code": "", "error": "", "state": ""}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query or "")
                result["code"] = (qs.get("code") or [""])[0]
                result["error"] = (qs.get("error") or [""])[0]
                result["state"] = (qs.get("state") or [""])[0]

                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Sentinel.AI: You can close this tab and return to the terminal.\n")
            finally:
                done.set()

        # Keep console output clean.
        def log_message(self, format, *args):
            return

    server = HTTPServer((host, port), Handler)

    def _serve_once():
        # Handle a single callback request then stop.
        server.handle_request()

    thread = threading.Thread(target=_serve_once, daemon=True)
    thread.start()

    full_url = f"{auth_url}?{urlencode(params)}"
    print("\nOpen this URL in your browser to grant access:\n")
    print(full_url)
    print("\nWaiting for OAuth callback on:", redirect_uri)

    # Try to open browser automatically (best effort).
    try:
        webbrowser.open(full_url)
    except Exception:
        pass

    done.wait(timeout=300)

    if result["error"]:
        raise RuntimeError(f"OAuth error: {result['error']}")
    if not result["code"]:
        raise RuntimeError("No OAuth code received. Did the consent flow complete?")
    if result["state"] and result["state"] != state:
        raise RuntimeError("OAuth state mismatch. Aborting for safety.")

    token_url = "https://oauth2.googleapis.com/token"
    token_payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": result["code"],
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            token_url,
            data=token_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Token exchange failed: HTTP {resp.status_code} {resp.text[:500]}")
        payload = resp.json()

    print("\nToken exchange response keys:", list(payload.keys()))
    refresh_token = payload.get("refresh_token") or ""
    access_token = payload.get("access_token") or ""

    if not refresh_token:
        print(
            "\nNo refresh_token returned.\n"
            "- This usually means the Google account already granted consent for this client+scopes.\n"
            "- Try again after revoking the app in Google Account -> Security -> Third-party access,\n"
            "  or ensure prompt=consent and access_type=offline are set.\n"
        )
        print("Access token was returned (short-lived):", bool(access_token))
        return

    print("\nSUCCESS. Add this line to backend/.env:\n")
    print(f"DRIVE_OAUTH_REFRESH_TOKEN={refresh_token}")
    print("\n(Keep it secret; do not commit it.)\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[OAuth Bootstrap] Failed: {e}\n")
