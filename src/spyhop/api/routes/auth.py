"""JWT authentication — login endpoint and token verification."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])

_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production-use-env-var")
_ALGORITHM = "HS256"
_TTL_MINUTES = int(os.environ.get("JWT_TTL_MINUTES", "480"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# Default operator password: "spyhop". Override via OPERATOR_PASSWORD_HASH env.
# Generate: python3 -c "import bcrypt; print(bcrypt.hashpw(b'pw', bcrypt.gensalt()).decode())"
_DEFAULT_HASH = (
    "$2b$12$HX8h9gGQ12VQYEs8IS9Fh.QY6pSP2MEfRgl5RXEbvE9t484rRMS1K"
)
_OPERATOR = {
    "username": os.environ.get("OPERATOR_USERNAME", "operator"),
    "hashed_password": os.environ.get(
        "OPERATOR_PASSWORD_HASH", _DEFAULT_HASH
    ).encode(),
}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = _TTL_MINUTES * 60


def _create_token(sub: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=_TTL_MINUTES)
    return jwt.encode(
        {"sub": sub, "exp": expire},
        _SECRET,
        algorithm=_ALGORITHM,
    )


def verify_token(token: str = Depends(oauth2_scheme)) -> str:
    """FastAPI dependency — returns username or raises 401."""
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
        username: str = payload.get("sub", "")
        if not username:
            raise ValueError("empty sub")
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/token", response_model=Token)
async def login(form: OAuth2PasswordRequestForm = Depends()) -> Token:
    """Exchange username + password for a JWT."""
    if form.username != _OPERATOR["username"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    pw_bytes = form.password.encode()
    stored_hash = _OPERATOR["hashed_password"]
    if not bcrypt.checkpw(pw_bytes, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(access_token=_create_token(form.username))


@router.get("/me")
async def me(username: str = Depends(verify_token)) -> dict[str, str]:
    return {"username": username}
