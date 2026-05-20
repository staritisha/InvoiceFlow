# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — auth.py
#  Enterprise-grade authentication & authorization layer.
#  Provides JWT access/refresh tokens, bcrypt password security, RBAC,
#  team-scoped access, and FastAPI dependency-based auth for all routers.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.database import SessionLocal

# ── Logger ────────────────────────────────────────────────────────────────────

logger = logging.getLogger("invoiceflow.auth")

# ── Constants (pulled from centralised config) ────────────────────────────────

SECRET_KEY: str = settings.SECRET_KEY
ALGORITHM: str = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS: int = settings.refresh_token_expire_days

# ── Password Hashing ──────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Return a bcrypt-hashed version of *password*."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify *plain_password* against a stored bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ── Role Definitions ──────────────────────────────────────────────────────────

class UserRole(str, Enum):
    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    MANAGER = "manager"
    MEMBER = "member"
    VIEWER = "viewer"
    AI_ASSISTANT = "ai_assistant"


# Role hierarchy — higher index = more privileged
_ROLE_HIERARCHY: list[UserRole] = [
    UserRole.VIEWER,
    UserRole.MEMBER,
    UserRole.MANAGER,
    UserRole.ADMIN,
    UserRole.SUPERADMIN,
]


def _role_level(role: str) -> int:
    try:
        return _ROLE_HIERARCHY.index(UserRole(role))
    except (ValueError, KeyError):
        return -1


def has_permission(user_role: str, required_role: str) -> bool:
    """Return True if *user_role* meets or exceeds *required_role* in hierarchy."""
    return _role_level(user_role) >= _role_level(required_role)


# ── Token Generation ──────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a signed JWT access token.

    Payload keys:
        sub   — user email
        role  — user role string
        type  — "access"
        exp   — expiry timestamp
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    logger.debug(f"[auth] Access token issued for sub={data.get('sub')} exp={expire.isoformat()}")
    return token


def create_refresh_token(data: dict) -> str:
    """
    Create a longer-lived JWT refresh token.

    Payload type is "refresh" so access tokens cannot be used as refresh tokens
    and vice-versa.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT. Raises HTTPException on any failure.
    Returns the raw payload dict.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as exc:
        logger.warning(f"[auth] Token decode failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── OAuth2 Scheme ─────────────────────────────────────────────────────────────

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ── DB Session for Auth ───────────────────────────────────────────────────────

def get_db_for_auth() -> Session:
    """Standalone DB session dependency used by auth functions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Core User Resolver ────────────────────────────────────────────────────────

def _resolve_user_from_token(token: str, db: Session) -> models.User:
    """
    Internal helper — decode token, load user from DB, enforce account status.
    Raises HTTPException for every invalid condition.
    """
    payload = decode_token(token)

    token_type = payload.get("type")
    if token_type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type — access token required",
        )

    email: Optional[str] = payload.get("sub")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is missing subject claim",
        )

    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive. Please contact support.",
        )

    return user


# ── FastAPI Auth Dependencies ─────────────────────────────────────────────────

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db_for_auth),
) -> models.User:
    """
    Dependency: resolve and return the currently authenticated user.
    Use with any protected route:

        @router.get("/me")
        def me(user: User = Depends(get_current_user)):
            ...
    """
    return _resolve_user_from_token(token, db)


def get_current_active_user(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """Alias of get_current_user — explicitly names the 'active' requirement."""
    return current_user


def get_admin_user(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """
    Dependency: allow only admin-level users (admin or superadmin).

        @router.delete("/users/{id}")
        def delete_user(admin: User = Depends(get_admin_user)):
            ...
    """
    if not has_permission(current_user.role, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def get_superadmin_user(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """Dependency: allow only superadmin users."""
    if current_user.role != UserRole.SUPERADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required",
        )
    return current_user


def get_manager_user(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """Dependency: allow manager, admin, or superadmin."""
    if not has_permission(current_user.role, UserRole.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager access or above required",
        )
    return current_user


def require_role(*roles: UserRole):
    """
    Factory that returns a FastAPI dependency enforcing any of the given roles.

    Usage:
        @router.post("/reports/generate")
        def generate(user = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER))):
            ...
    """
    def _dependency(current_user: models.User = Depends(get_current_user)) -> models.User:
        if current_user.role not in [r.value for r in roles]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"One of these roles is required: {[r.value for r in roles]}",
            )
        return current_user
    return _dependency


# ── Team-Scoped Access ────────────────────────────────────────────────────────

def get_team_member(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """
    Dependency: ensure the user belongs to a team (team_id is set).
    Use on routes that require team workspace context.
    """
    if not getattr(current_user, "team_id", None):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be a member of a team to access this resource",
        )
    return current_user


def verify_team_access(user: models.User, team_id: int) -> None:
    """
    Utility: raise 403 if *user* does not belong to *team_id*.
    Call inline from any service or route handler.
    """
    if getattr(user, "team_id", None) != team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this team's resources",
        )


# ── Invoice Ownership ─────────────────────────────────────────────────────────

def verify_invoice_ownership(
    invoice: models.Invoice,
    current_user: models.User,
    allow_admin: bool = True,
) -> None:
    """
    Raise 403 unless *current_user* owns *invoice* (or is admin if allow_admin).
    """
    is_owner = invoice.user_id == current_user.id
    is_admin = allow_admin and has_permission(current_user.role, UserRole.ADMIN)
    if not (is_owner or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this invoice",
        )


# ── Refresh Token Handler ─────────────────────────────────────────────────────

def refresh_access_token(refresh_token: str, db: Session) -> str:
    """
    Validate a refresh token and return a new access token.
    Raises HTTPException if the refresh token is invalid or the user is gone.
    """
    try:
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is invalid or has expired",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type — refresh token required",
        )

    email: Optional[str] = payload.get("sub")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    logger.info(f"[auth] Refresh token used — issuing new access token for {email}")
    return create_access_token({"sub": user.email, "role": user.role})


# ── WebSocket Auth ────────────────────────────────────────────────────────────

async def get_ws_user(
    websocket: WebSocket,
    db: Session,
    token: Optional[str] = None,
) -> Optional[models.User]:
    """
    Authenticate a WebSocket connection from a Bearer token passed as a
    query param (?token=...) or the first message after connect.

    Returns the User or None if auth fails (caller decides whether to close).

    Usage in WebSocket endpoint:
        user = await get_ws_user(ws, db, token=ws.query_params.get("token"))
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        email = payload.get("sub")
        if not email:
            return None
        user = db.query(models.User).filter(models.User.email == email).first()
        if user and user.is_active:
            return user
    except JWTError:
        pass
    return None


# ── Optional Auth (AI assistant / public routes) ──────────────────────────────

def get_optional_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db_for_auth),
) -> Optional[models.User]:
    """
    Dependency: returns the current user if authenticated, or None.
    Use on routes that work for both authenticated and anonymous visitors.
    """
    if not token:
        return None
    try:
        return _resolve_user_from_token(token, db)
    except HTTPException:
        return None


# ── Session Context Extractor ─────────────────────────────────────────────────

def extract_user_context(current_user: models.User) -> dict:
    """
    Return a serializable context dict from the current user.
    Useful for passing into AI services, loggers, and audit trails.
    """
    return {
        "user_id": current_user.id,
        "email": current_user.email,
        "role": current_user.role,
        "team_id": getattr(current_user, "team_id", None),
        "full_name": getattr(current_user, "full_name", None),
        "subscription_tier": getattr(current_user, "subscription_tier", "free"),
    }


# ── Future OAuth Placeholders ─────────────────────────────────────────────────
# These stubs allow the OAuth routes to exist in routers/auth.py without
# breaking imports. Replace the bodies with real provider flows when ready.

def google_oauth_callback(code: str, db: Session) -> dict:
    """
    Placeholder: exchange Google OAuth *code* for an access token.
    Implement with `authlib` or `httpx` when ready.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Google OAuth is not yet configured",
    )


def github_oauth_callback(code: str, db: Session) -> dict:
    """
    Placeholder: exchange GitHub OAuth *code* for an access token.
    Implement with `authlib` or `httpx` when ready.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="GitHub OAuth is not yet configured",
    )
