"""
auth.py - Gmail API Authentication via OAuth 2.0
--------------------------------------------------
HOW IT WORKS
  • First run  → opens a browser for you to approve Gmail access → saves token.json
  • Later runs → refreshes token automatically (no browser needed)

SETUP CHECKLIST (do once):
  1. Google Cloud Console → https://console.cloud.google.com
  2. Create project → Enable "Gmail API"
  3. OAuth consent screen → External → add your Gmail as test user
  4. Credentials → Create OAuth client ID → Desktop App → download → save as credentials.json
  5. pip install -r requirements.txt
  6. python auth.py   ← browser opens, approve, token.json saved
"""

import os

# ── Windows Corporate Proxy SSL Fix ───────────────────────────────────────────
# Python's ssl module uses its own CA bundle (certifi) by default and ignores
# the Windows certificate store.  Corporate proxies inject their own CA into
# Windows' trust store.  `truststore.inject_into_ssl()` patches Python's ssl
# at runtime to also trust the Windows store, which resolves
# "[SSL: WRONG_VERSION_NUMBER]" and similar certificate-chain errors.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass  # truststore not installed — continue without the patch

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Scopes ─────────────────────────────────────────────────────────────────────
# readonly is enough for searching + downloading — change if you also want to label/delete
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",   # needed to mark emails as read after processing
    "https://www.googleapis.com/auth/gmail.send",     # needed to send result emails
]

CREDENTIALS_FILE = "credentials.json"   # downloaded from Google Cloud Console
TOKEN_FILE       = "token.json"         # auto-created after first login


def get_gmail_service():
    """
    Build and return an authenticated Gmail API service object.
    Handles first-time browser auth and automatic token refresh.
    """
    creds = None

    # Load saved token if it exists
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Refresh or re-authenticate if needed
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"[FAIL] '{CREDENTIALS_FILE}' not found.\n"
                    "   Download it from Google Cloud Console:\n"
                    "   APIs & Services -> Credentials -> OAuth 2.0 Client IDs -> Download JSON\n"
                    "   Save it as 'credentials.json' in this folder."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save token for future runs
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ── Module-level service (imported by tools.py) ───────────────────────────────
gmail = get_gmail_service()


# ── Quick connection test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    profile = gmail.users().getProfile(userId="me").execute()
    print(f"[OK] Gmail Connected Successfully!")
    print(f"   Email   : {profile['emailAddress']}")
    print(f"   Messages: {profile['messagesTotal']:,}")
    print(f"   Threads : {profile['threadsTotal']:,}")
