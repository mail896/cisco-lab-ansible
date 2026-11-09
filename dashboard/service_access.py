"""Local service helpers use an owner-only credential, never a browser fallback."""
import base64
from pathlib import Path


def authorization(state=None):
    state = state or Path.home() / '.local/state/cisco-lab-dashboard'
    token = state / 'auth/automation-token.json'
    if token.exists():
        from dashboard.auth import read_settings_token
        return 'Bearer ' + read_settings_token(token)
    # Compatibility for an installation that has not configured OIDC yet.
    from dashboard.server import read_credential
    return 'Basic ' + base64.b64encode(read_credential(state / 'passwort')).decode()
