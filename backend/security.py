"""
security.py - passwords, login tokens and role checks.

How login works (simple version):
  1. User sends username + password to POST /auth/login
  2. We compare the password with the stored HASH (we never store real passwords)
  3. If correct we return a TOKEN (a signed string = "digital wristband")
  4. The frontend sends that token with every later request
  5. `get_current_user` reads the token and finds who is calling
  6. `require_roles("doctor")` refuses anyone who is not a doctor
"""
import hashlib
import hmac
import os
from datetime import timedelta

import jwt  # PyJWT
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db, utcnow
from backend.models import User

ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------- passwords --
def hash_password(password: str) -> str:
    """Salted PBKDF2 hash. Result looks like 'salt$hash' (both hex)."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 120_000)
        return hmac.compare_digest(digest.hex(), hash_hex)  # constant-time compare
    except ValueError:
        return False


# ------------------------------------------------------------------- tokens --
def create_access_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "exp": utcnow() + timedelta(minutes=settings.TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: returns the logged-in User or raises 401."""
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in or session expired")
    if creds is None:
        raise unauthorized
    try:
        payload = jwt.decode(creds.credentials, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    if user is None or user.status != "active":
        raise unauthorized
    return user


def require_roles(*roles: str):
    """
    Build a dependency that only lets certain roles in.
    Example:  user: User = Depends(require_roles("doctor", "admin"))
    """

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{user.role}' is not allowed here")
        return user

    return checker
