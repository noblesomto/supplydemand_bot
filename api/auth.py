import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from config import settings

_security = HTTPBasic(auto_error=False)


def require_dashboard_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    """HTTP Basic Auth for the dashboard routes (same scheme as harmonic_bot).
    A no-op when DASHBOARD_USERNAME/PASSWORD are unset (local dev, tests);
    main.py refuses to bind a non-localhost HOST without them."""
    if not settings.DASHBOARD_USERNAME or not settings.DASHBOARD_PASSWORD:
        return
    valid = (credentials is not None
             and secrets.compare_digest(credentials.username, settings.DASHBOARD_USERNAME)
             and secrets.compare_digest(credentials.password, settings.DASHBOARD_PASSWORD))
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})
