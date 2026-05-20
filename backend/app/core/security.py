# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — app/core/security.py
#  Production-grade security backbone: cryptography, token lifecycle,
#  RBAC enforcement, password policy, ownership guards, and OAuth stubs.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import hashlib
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from fastapi import HTTPException, Request, WebSocket, status
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# ── Logger ────────────────────────────────────────────────────────────────────

import logging
logger = logging.getLogger("invoiceflow.security")

# ── Constants ─────────────────────────────────────────────────────────────────

SECRET_KEY: str = settings.SECRET_KEY
ALGORITHM: str = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS: int = settings.refresh_token_expire_days

EMAIL_VERIFY_TOKEN_EXPIRE_HOURS: int = 24
PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

# ═══════════════════════════════════════════════════════════════════════════════
#  PASSWORD HASHING
# ═══════════════════════════════════════════════════════════════════════════════

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password*."""
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify *plain* against a stored bcrypt *hashed* value."""
    return _pwd_context.verify(plain, hashed)


# ── Password Policy ───────────────────────────────────────────────────────────

def validate_password_strength(password: str) -> tuple[bool, list[str]]:
    """
    Validate password against SaaS-grade policy.
    Returns (is_valid, list_of_violations).
    """
    violations: list[str] = []
    if len(password) < 8:
        violations.append("At least 8 characters required")
    if not re.search(r"[A-Z]", password):
        violations.append("At least one uppercase letter required")
    if not re.search(r"[a-z]", password):
        violations.append("At least one lowercase letter required")
    if not re.search(r"\d", password):
        violations.append("At least one digit required")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]", password):
        violations.append("At least one special character required")
    return (len(violations) == 0, violations)


def enforce_password_policy(password: str) -> None:
    """Raise HTTPException 422 if password does not meet policy."""
    valid, violations = validate_password_strength(password)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Password does not meet security policy", "violations": violations},
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  ROLE-BASED ACCESS CONTROL
# ═══════════════════════════════════════════════════════════════════════════════

class UserRole(str, Enum):
    SUPERADMIN  = "superadmin"
    ADMIN       = "admin"
    MANAGER     = "manager"
    MEMBER      = "member"
    VIEWER      = "viewer"
    AI_ASSISTANT = "ai_assistant"


_ROLE_HIERARCHY: list[str] = [
    UserRole.VIEWER,
    UserRole.MEMBER,
    UserRole.MANAGER,
    UserRole.ADMIN,
    UserRole.SUPERADMIN,
]


def role_level(role: str) -> int:
    try:
        return _ROLE_HIERARCHY.index(role)
    except ValueError:
        return -1


def has_permission(user_role: str, required_role: str) -> bool:
    """Return True if *user_role* meets or exceeds *required_role*."""
    return role_level(user_role) >= role_level(required_role)


def require_permission(user_role: str, required_role: str, resource: str = "resource") -> None:
    """Raise 403 if *user_role* does not meet *required_role*."""
    if not has_permission(user_role, required_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{required_role}' or above required to access {resource}",
        )


def is_admin(role: str) -> bool:
    return has_permission(role, UserRole.ADMIN)


def is_superadmin(role: str) -> bool:
    return role == UserRole.SUPERADMIN


# ═══════════════════════════════════════════════════════════════════════════════
#  JWT TOKEN CREATION
# ═══════════════════════════════════════════════════════════════════════════════

def _build_token(
    data: dict,
    token_type: str,
    expires_delta: timedelta,
    extra_claims: Optional[dict] = None,
) -> str:
    payload = data.copy()
    payload.update({
        "type": token_type,
        "exp": datetime.now(timezone.utc) + expires_delta,
        "iat": datetime.now(timezone.utc),
        **(extra_claims or {}),
    })
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[dict] = None,
) -> str:
    """
    Create a signed JWT access token.
    Payload includes: sub, role, team_id, type='access', exp, iat.
    """
    delta = expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    token = _build_token(data, "access", delta, extra_claims)
    logger.debug(f"[security] Access token issued for sub={data.get('sub')}")
    return token


def create_refresh_token(data: dict) -> str:
    """
    Create a long-lived JWT refresh token (type='refresh').
    Cannot be used in place of an access token.
    """
    return _build_token(data, "refresh", timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))


def create_email_verification_token(email: str) -> str:
    """Create a short-lived token for email address verification."""
    return _build_token(
        {"sub": email},
        "email_verify",
        timedelta(hours=EMAIL_VERIFY_TOKEN_EXPIRE_HOURS),
    )


def create_password_reset_token(email: str) -> str:
    """Create a short-lived token for password reset flows."""
    return _build_token(
        {"sub": email},
        "password_reset",
        timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  JWT TOKEN DECODING & VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT. Raises 401 on any failure (expired, tampered, invalid).
    Returns the raw payload dict.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as exc:
        logger.warning(f"[security] Token decode failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )


def validate_token_type(payload: dict, expected_type: str) -> None:
    """Raise 401 if the token's 'type' claim does not match *expected_type*."""
    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token type. Expected '{expected_type}'.",
        )


def decode_access_token(token: str) -> dict:
    payload = decode_token(token)
    validate_token_type(payload, "access")
    return payload


def decode_refresh_token(token: str) -> dict:
    payload = decode_token(token)
    validate_token_type(payload, "refresh")
    return payload


def decode_email_verify_token(token: str) -> str:
    """Decode email verification token and return the email address."""
    payload = decode_token(token)
    validate_token_type(payload, "email_verify")
    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=400, detail="Invalid verification token")
    return email


def decode_password_reset_token(token: str) -> str:
    """Decode password reset token and return the email address."""
    payload = decode_token(token)
    validate_token_type(payload, "password_reset")
    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=400, detail="Invalid reset token")
    return email


def rotate_access_token(refresh_token: str) -> str:
    """
    Issue a new access token from a valid refresh token.
    Used in the POST /auth/refresh endpoint.
    """
    payload = decode_refresh_token(refresh_token)
    sub = payload.get("sub")
    role = payload.get("role")
    team_id = payload.get("team_id")

    if not sub:
        raise HTTPException(status_code=401, detail="Refresh token payload invalid")

    return create_access_token({"sub": sub, "role": role, "team_id": team_id})


# ═══════════════════════════════════════════════════════════════════════════════
#  SECURE RANDOM TOKENS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_secure_token(length: int = 32) -> str:
    """Return a cryptographically secure URL-safe token."""
    return secrets.token_urlsafe(length)


def generate_secure_otp(digits: int = 6) -> str:
    """Return a numeric OTP of *digits* length (for MFA/TOTP stubs)."""
    return "".join(secrets.choice(string.digits) for _ in range(digits))


def generate_random_string(length: int = 16, charset: Optional[str] = None) -> str:
    alphabet = charset or (string.ascii_letters + string.digits)
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_token(token: str) -> str:
    """Return a SHA-256 hex digest of *token* — for safe DB storage of reset tokens."""
    return hashlib.sha256(token.encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
#  WEBSOCKET AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════════

def extract_ws_token(websocket: WebSocket) -> Optional[str]:
    """
    Extract a Bearer token from a WebSocket connection.
    Checks query param `?token=` first, then the Authorization header.
    """
    token = websocket.query_params.get("token")
    if not token:
        auth_header = websocket.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
    return token or None


def validate_ws_token(token: Optional[str]) -> Optional[dict]:
    """
    Validate a WebSocket bearer token.
    Returns the decoded payload or None if invalid (caller decides whether to close).
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  REQUEST IDENTITY EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def extract_bearer_token(request: Request) -> Optional[str]:
    """Pull the Bearer token string from an HTTP request's Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1]
    return None


def extract_request_identity(request: Request) -> dict:
    """
    Return a dict of identity metadata from an incoming request.
    Useful for audit logs and rate limiters.
    """
    client_ip = request.headers.get("X-Forwarded-For", "")
    if not client_ip:
        client_ip = getattr(request.client, "host", "unknown")
    return {
        "ip": client_ip.split(",")[0].strip(),
        "user_agent": request.headers.get("User-Agent", ""),
        "request_id": getattr(request.state, "request_id", None),
        "path": str(request.url.path),
        "method": request.method,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  OWNERSHIP & RESOURCE GUARDS
# ═══════════════════════════════════════════════════════════════════════════════

def verify_resource_ownership(
    resource_user_id: int,
    current_user_id: int,
    current_user_role: str,
    resource_name: str = "resource",
    allow_admin: bool = True,
) -> None:
    """
    Raise 403 unless the current user owns the resource or is an admin.
    Works for invoices, conversations, notifications, reports, widgets, etc.
    """
    is_owner = resource_user_id == current_user_id
    is_privileged = allow_admin and is_admin(current_user_role)
    if not (is_owner or is_privileged):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You do not have access to this {resource_name}",
        )


def verify_team_isolation(
    resource_team_id: Optional[int],
    current_team_id: Optional[int],
    current_user_role: str,
    resource_name: str = "resource",
) -> None:
    """
    Raise 403 if the resource belongs to a different team.
    Superadmins bypass team isolation.
    """
    if is_superadmin(current_user_role):
        return
    if resource_team_id and resource_team_id != current_team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This {resource_name} belongs to a different team",
        )


def verify_invoice_access(invoice, current_user) -> None:
    """Shorthand: verify ownership of an invoice object."""
    verify_resource_ownership(
        invoice.user_id,
        current_user.id,
        current_user.role,
        resource_name="invoice",
    )


def verify_notification_access(notification, current_user) -> None:
    """Shorthand: ensure a user can only read their own notifications."""
    verify_resource_ownership(
        notification.user_id,
        current_user.id,
        current_user.role,
        resource_name="notification",
        allow_admin=False,
    )


def verify_analytics_access(current_user) -> None:
    """Analytics is available to manager-level and above."""
    require_permission(current_user.role, UserRole.MANAGER, resource="analytics")


def verify_ai_memory_access(conversation, current_user) -> None:
    """AI conversation history is strictly owner-only."""
    verify_resource_ownership(
        conversation.user_id,
        current_user.id,
        current_user.role,
        resource_name="AI conversation",
        allow_admin=False,
    )


def prevent_impersonation(actor_user_id: int, target_user_id: int, actor_role: str) -> None:
    """
    Raise 403 if a non-superadmin tries to act as another user.
    Prevents privilege escalation and impersonation attacks.
    """
    if actor_user_id != target_user_id and not is_superadmin(actor_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot perform actions on behalf of another user",
        )


def verify_ai_workflow_access(workflow, current_user) -> None:
    """AI workflow execution requires manager-level or team ownership."""
    team_id = getattr(workflow, "team_id", None)
    user_team_id = getattr(current_user, "team_id", None)

    if is_admin(current_user.role):
        return
    if team_id and team_id != user_team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this workflow",
        )
    require_permission(current_user.role, UserRole.MANAGER, resource="workflow execution")


def verify_dashboard_access(widget, current_user) -> None:
    """Dashboard widgets are per-user."""
    verify_resource_ownership(
        widget.user_id,
        current_user.id,
        current_user.role,
        resource_name="dashboard widget",
        allow_admin=False,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  SESSION CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════

def build_token_claims(user) -> dict:
    """
    Build the JWT payload claims dict from a User model instance.
    Use as: create_access_token(build_token_claims(user))
    """
    return {
        "sub": user.email,
        "role": getattr(user, "role", UserRole.MEMBER),
        "team_id": getattr(user, "team_id", None),
        "user_id": user.id,
        "subscription_tier": getattr(user, "subscription_tier", "free"),
    }


def extract_claims(token: str) -> dict:
    """Decode a token and return its claims without raising on missing sub."""
    try:
        return decode_token(token)
    except HTTPException:
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
#  FUTURE OAUTH / MFA STUBS
# ═══════════════════════════════════════════════════════════════════════════════

def google_oauth_exchange(code: str) -> dict:
    """
    [Stub] Exchange a Google OAuth authorization code for tokens.
    Implement with `authlib` or `httpx` + Google's token endpoint.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Google OAuth is not yet configured",
    )


def github_oauth_exchange(code: str) -> dict:
    """
    [Stub] Exchange a GitHub OAuth authorization code for tokens.
    Implement with `authlib` or GitHub's token endpoint.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="GitHub OAuth is not yet configured",
    )


def sso_saml_assertion(assertion: str) -> dict:
    """
    [Stub] Parse and validate a SAML assertion for enterprise SSO.
    Implement with `python-saml` when ready.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="SAML SSO is not yet configured",
    )


def verify_totp(secret: str, code: str) -> bool:
    """
    [Stub] Verify a TOTP code for MFA.
    Implement with `pyotp` when ready.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="MFA/TOTP is not yet enabled",
    )
