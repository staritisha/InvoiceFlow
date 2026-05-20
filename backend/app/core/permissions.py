# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — app/core/permissions.py
#  Production-grade RBAC: role hierarchy, permission matrix, ownership guards,
#  subscription-tier gates, multi-tenant isolation, and decorator factories.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional

from fastapi import Depends, HTTPException, status

logger = logging.getLogger("invoiceflow.permissions")


# ═══════════════════════════════════════════════════════════════════════════════
#  ROLE DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

class UserRole(str, Enum):
    SUPERADMIN   = "superadmin"
    ADMIN        = "admin"
    MANAGER      = "manager"
    MEMBER       = "member"
    VIEWER       = "viewer"
    AI_ASSISTANT = "ai_assistant"


class SubscriptionTier(str, Enum):
    FREE       = "free"
    STARTER    = "starter"
    PRO        = "pro"
    ENTERPRISE = "enterprise"


# Ordered lowest → highest privilege
_ROLE_HIERARCHY: list[str] = [
    UserRole.VIEWER,
    UserRole.AI_ASSISTANT,
    UserRole.MEMBER,
    UserRole.MANAGER,
    UserRole.ADMIN,
    UserRole.SUPERADMIN,
]

_TIER_HIERARCHY: list[str] = [
    SubscriptionTier.FREE,
    SubscriptionTier.STARTER,
    SubscriptionTier.PRO,
    SubscriptionTier.ENTERPRISE,
]


def role_level(role: str) -> int:
    try:
        return _ROLE_HIERARCHY.index(role)
    except ValueError:
        return -1


def tier_level(tier: str) -> int:
    try:
        return _TIER_HIERARCHY.index(tier)
    except ValueError:
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
#  PERMISSION MATRIX
# ═══════════════════════════════════════════════════════════════════════════════
#  Format:  "feature:action" → minimum UserRole required
#  Actions: read, write, delete, execute, admin

PERMISSION_MATRIX: dict[str, str] = {

    # ── Invoices ──────────────────────────────────────────────────────────────
    "invoices:read":           UserRole.VIEWER,
    "invoices:write":          UserRole.MEMBER,
    "invoices:delete":         UserRole.MANAGER,
    "invoices:send":           UserRole.MEMBER,
    "invoices:duplicate":      UserRole.MEMBER,
    "invoices:export":         UserRole.MEMBER,
    "invoices:admin":          UserRole.ADMIN,

    # ── Clients / Customers ───────────────────────────────────────────────────
    "clients:read":            UserRole.VIEWER,
    "clients:write":           UserRole.MEMBER,
    "clients:delete":          UserRole.MANAGER,
    "clients:risk_data":       UserRole.MANAGER,
    "clients:admin":           UserRole.ADMIN,

    # ── Payments ──────────────────────────────────────────────────────────────
    "payments:read":           UserRole.VIEWER,
    "payments:write":          UserRole.MEMBER,
    "payments:stripe_admin":   UserRole.ADMIN,

    # ── Analytics ─────────────────────────────────────────────────────────────
    "analytics:read":          UserRole.MEMBER,
    "analytics:revenue":       UserRole.MANAGER,
    "analytics:team":          UserRole.MANAGER,
    "analytics:export":        UserRole.MANAGER,
    "analytics:admin":         UserRole.ADMIN,

    # ── Reports ───────────────────────────────────────────────────────────────
    "reports:read":            UserRole.MEMBER,
    "reports:write":           UserRole.MANAGER,
    "reports:delete":          UserRole.MANAGER,
    "reports:download":        UserRole.MEMBER,
    "reports:admin":           UserRole.ADMIN,

    # ── Reminders ─────────────────────────────────────────────────────────────
    "reminders:read":          UserRole.VIEWER,
    "reminders:write":         UserRole.MEMBER,
    "reminders:send":          UserRole.MEMBER,
    "reminders:delete":        UserRole.MANAGER,

    # ── Workflows ─────────────────────────────────────────────────────────────
    "workflows:read":          UserRole.MEMBER,
    "workflows:write":         UserRole.MANAGER,
    "workflows:execute":       UserRole.MANAGER,
    "workflows:delete":        UserRole.ADMIN,
    "workflows:admin":         UserRole.ADMIN,

    # ── AI Features ───────────────────────────────────────────────────────────
    "ai:chat":                 UserRole.MEMBER,
    "ai:command_center":       UserRole.MEMBER,
    "ai:insights":             UserRole.MANAGER,
    "ai:insights_generate":    UserRole.MANAGER,
    "ai:memory_read":          UserRole.MEMBER,
    "ai:recommendations":      UserRole.MEMBER,
    "ai:actions":              UserRole.MEMBER,
    "ai:voice":                UserRole.MEMBER,
    "ai:invoice_generate":     UserRole.MEMBER,
    "ai:admin":                UserRole.ADMIN,

    # ── Notifications ─────────────────────────────────────────────────────────
    "notifications:read":      UserRole.VIEWER,
    "notifications:write":     UserRole.MEMBER,
    "notifications:delete":    UserRole.MEMBER,
    "notifications:admin":     UserRole.ADMIN,

    # ── Dashboard & Widgets ───────────────────────────────────────────────────
    "dashboard:read":          UserRole.VIEWER,
    "dashboard:write":         UserRole.MEMBER,
    "dashboard:widgets_admin": UserRole.ADMIN,

    # ── Financial Data ────────────────────────────────────────────────────────
    "financial:read":          UserRole.MANAGER,
    "financial:export":        UserRole.MANAGER,
    "financial:admin":         UserRole.ADMIN,

    # ── Audit Logs ────────────────────────────────────────────────────────────
    "audit:read":              UserRole.ADMIN,
    "audit:export":            UserRole.SUPERADMIN,

    # ── User Management ───────────────────────────────────────────────────────
    "users:read":              UserRole.MANAGER,
    "users:write":             UserRole.ADMIN,
    "users:delete":            UserRole.ADMIN,
    "users:admin":             UserRole.SUPERADMIN,

    # ── Team Management ───────────────────────────────────────────────────────
    "teams:read":              UserRole.MEMBER,
    "teams:write":             UserRole.ADMIN,
    "teams:delete":            UserRole.SUPERADMIN,

    # ── Integrations ──────────────────────────────────────────────────────────
    "integrations:read":       UserRole.MANAGER,
    "integrations:write":      UserRole.ADMIN,

    # ── Real-time / WebSockets ────────────────────────────────────────────────
    "websocket:connect":       UserRole.VIEWER,
    "websocket:broadcast":     UserRole.ADMIN,

    # ── Background Tasks ──────────────────────────────────────────────────────
    "scheduler:read":          UserRole.ADMIN,
    "scheduler:trigger":       UserRole.ADMIN,
    "scheduler:admin":         UserRole.SUPERADMIN,

    # ── Platform Admin ────────────────────────────────────────────────────────
    "platform:metrics":        UserRole.ADMIN,
    "platform:system_info":    UserRole.ADMIN,
    "platform:superadmin":     UserRole.SUPERADMIN,
}

# ── Subscription-tier gates ───────────────────────────────────────────────────
# Features locked behind a minimum subscription tier

TIER_FEATURE_GATES: dict[str, str] = {
    "ai:voice":                SubscriptionTier.STARTER,
    "ai:command_center":       SubscriptionTier.PRO,
    "ai:insights_generate":    SubscriptionTier.PRO,
    "ai:admin":                SubscriptionTier.ENTERPRISE,
    "reports:download":        SubscriptionTier.STARTER,
    "analytics:export":        SubscriptionTier.PRO,
    "financial:export":        SubscriptionTier.PRO,
    "integrations:write":      SubscriptionTier.PRO,
    "audit:read":              SubscriptionTier.ENTERPRISE,
    "websocket:broadcast":     SubscriptionTier.ENTERPRISE,
}


# ═══════════════════════════════════════════════════════════════════════════════
#  CORE PERMISSION CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

def has_permission(role: str, permission: str) -> bool:
    """
    Return True if *role* meets or exceeds the minimum role required for *permission*.
    Superadmin always passes. Unknown permissions default to admin-required.
    """
    if role == UserRole.SUPERADMIN:
        return True
    required = PERMISSION_MATRIX.get(permission, UserRole.ADMIN)
    return role_level(role) >= role_level(required)


def has_tier_access(tier: str, permission: str) -> bool:
    """Return True if *tier* meets the subscription requirement for *permission*."""
    required = TIER_FEATURE_GATES.get(permission)
    if not required:
        return True   # no gate configured
    return tier_level(tier) >= tier_level(required)


def check_permission(
    role: str,
    permission: str,
    tier: Optional[str] = None,
    resource: Optional[str] = None,
) -> None:
    """
    Raise HTTPException if *role* lacks *permission* or the tier gate blocks access.
    *resource* is used only for the error message.
    """
    if not has_permission(role, permission):
        required = PERMISSION_MATRIX.get(permission, "admin")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": f"Insufficient permissions for '{resource or permission}'",
                "required_role": required,
                "your_role": role,
                "permission": permission,
            },
        )

    if tier and not has_tier_access(tier, permission):
        required_tier = TIER_FEATURE_GATES.get(permission, "pro")
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": "This feature requires a higher subscription tier",
                "required_tier": required_tier,
                "your_tier": tier,
                "permission": permission,
            },
        )


def get_user_permissions(role: str) -> list[str]:
    """Return all permission strings the given role can access."""
    return [perm for perm in PERMISSION_MATRIX if has_permission(role, perm)]


def get_tier_permissions(tier: str) -> list[str]:
    """Return all tier-gated permissions accessible at *tier*."""
    return [perm for perm in TIER_FEATURE_GATES if has_tier_access(tier, perm)]


# ═══════════════════════════════════════════════════════════════════════════════
#  FASTAPI DEPENDENCY FACTORIES
# ═══════════════════════════════════════════════════════════════════════════════

def require_permission(permission: str, resource: Optional[str] = None):
    """
    FastAPI dependency factory. Injects the current user and checks *permission*.

    Usage:
        @router.delete("/invoices/{id}")
        def delete(user = Depends(require_permission("invoices:delete"))):
            ...
    """
    from app.auth import get_current_user  # local import avoids circular deps

    def _dependency(current_user=Depends(get_current_user)):
        tier = getattr(current_user, "subscription_tier", SubscriptionTier.FREE)
        check_permission(current_user.role, permission, tier=tier, resource=resource)
        return current_user

    _dependency.__name__ = f"require_{permission.replace(':', '_')}"
    return _dependency


def require_any_permission(*permissions: str):
    """
    Dependency: passes if the user has AT LEAST ONE of the given permissions.

    Usage:
        @router.get("/analytics")
        def analytics(user = Depends(require_any_permission("analytics:read", "analytics:revenue"))):
            ...
    """
    from app.auth import get_current_user

    def _dependency(current_user=Depends(get_current_user)):
        tier = getattr(current_user, "subscription_tier", SubscriptionTier.FREE)
        for perm in permissions:
            if has_permission(current_user.role, perm) and has_tier_access(tier, perm):
                return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"One of these permissions is required: {list(permissions)}",
        )

    return _dependency


def require_role_at_least(minimum_role: UserRole):
    """
    Dependency: passes if the user's role is at or above *minimum_role*.

    Usage:
        @router.post("/workflows")
        def create(user = Depends(require_role_at_least(UserRole.MANAGER))):
            ...
    """
    from app.auth import get_current_user

    def _dependency(current_user=Depends(get_current_user)):
        if role_level(current_user.role) < role_level(minimum_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{minimum_role}' or above required",
            )
        return current_user

    _dependency.__name__ = f"require_{minimum_role}_or_above"
    return _dependency


def require_subscription(minimum_tier: SubscriptionTier):
    """
    Dependency: blocks users below *minimum_tier*.

    Usage:
        @router.post("/ai/command")
        def command(user = Depends(require_subscription(SubscriptionTier.PRO))):
            ...
    """
    from app.auth import get_current_user

    def _dependency(current_user=Depends(get_current_user)):
        tier = getattr(current_user, "subscription_tier", SubscriptionTier.FREE)
        if tier_level(tier) < tier_level(minimum_tier):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "message": f"This feature requires the '{minimum_tier}' plan or above",
                    "your_tier": tier,
                    "required_tier": minimum_tier,
                    "upgrade_url": "/billing/upgrade",
                },
            )
        return current_user

    return _dependency


# ═══════════════════════════════════════════════════════════════════════════════
#  OWNERSHIP GUARDS
# ═══════════════════════════════════════════════════════════════════════════════

def assert_owner_or_admin(
    resource_user_id: int,
    current_user_id: int,
    current_role: str,
    resource_name: str = "resource",
) -> None:
    """
    Raise 403 unless the user owns the resource or has admin-level access.
    """
    if current_role == UserRole.SUPERADMIN:
        return
    if resource_user_id == current_user_id:
        return
    if has_permission(current_role, "invoices:admin"):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"You do not have access to this {resource_name}",
    )


def assert_team_member(
    resource_team_id: Optional[int],
    current_team_id: Optional[int],
    current_role: str,
    resource_name: str = "resource",
) -> None:
    """
    Raise 403 if resource belongs to a different team.
    Superadmin bypasses.
    """
    if current_role == UserRole.SUPERADMIN:
        return
    if resource_team_id is not None and resource_team_id != current_team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This {resource_name} belongs to a different team workspace",
        )


def assert_owner_only(
    resource_user_id: int,
    current_user_id: int,
    resource_name: str = "resource",
) -> None:
    """Strict owner-only check — even admins are blocked."""
    if resource_user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only the owner can access this {resource_name}",
        )


# ── Specific resource guards ──────────────────────────────────────────────────

def guard_invoice(invoice, current_user) -> None:
    assert_owner_or_admin(invoice.user_id, current_user.id, current_user.role, "invoice")
    assert_team_member(
        getattr(invoice, "team_id", None),
        getattr(current_user, "team_id", None),
        current_user.role,
        "invoice",
    )


def guard_client(client, current_user) -> None:
    assert_team_member(
        getattr(client, "team_id", None),
        getattr(current_user, "team_id", None),
        current_user.role,
        "client",
    )


def guard_report(report, current_user) -> None:
    assert_owner_or_admin(
        getattr(report, "created_by", report.id),
        current_user.id,
        current_user.role,
        "report",
    )


def guard_notification(notification, current_user) -> None:
    assert_owner_only(notification.user_id, current_user.id, "notification")


def guard_ai_memory(conversation, current_user) -> None:
    assert_owner_only(conversation.user_id, current_user.id, "AI conversation")


def guard_dashboard_widget(widget, current_user) -> None:
    assert_owner_only(widget.user_id, current_user.id, "dashboard widget")


def guard_workflow(workflow, current_user) -> None:
    check_permission(current_user.role, "workflows:execute", resource="workflow")
    assert_team_member(
        getattr(workflow, "team_id", None),
        getattr(current_user, "team_id", None),
        current_user.role,
        "workflow",
    )


def guard_analytics(current_user) -> None:
    check_permission(current_user.role, "analytics:revenue", resource="revenue analytics")


def guard_financial_export(current_user) -> None:
    tier = getattr(current_user, "subscription_tier", SubscriptionTier.FREE)
    check_permission(current_user.role, "financial:export", tier=tier, resource="financial export")


def guard_audit_log(current_user) -> None:
    tier = getattr(current_user, "subscription_tier", SubscriptionTier.FREE)
    check_permission(current_user.role, "audit:read", tier=tier, resource="audit log")


def guard_client_risk_data(current_user) -> None:
    check_permission(current_user.role, "clients:risk_data", resource="client risk data")


def guard_payment_processing(current_user) -> None:
    check_permission(current_user.role, "payments:stripe_admin", resource="payment processing")


def guard_ai_insights(current_user) -> None:
    tier = getattr(current_user, "subscription_tier", SubscriptionTier.FREE)
    check_permission(current_user.role, "ai:insights_generate", tier=tier, resource="AI insights")


def guard_websocket_broadcast(current_user) -> None:
    tier = getattr(current_user, "subscription_tier", SubscriptionTier.FREE)
    check_permission(current_user.role, "websocket:broadcast", tier=tier, resource="WebSocket broadcast")


def guard_scheduler(current_user) -> None:
    check_permission(current_user.role, "scheduler:trigger", resource="scheduler")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEAM ADMIN DELEGATION
# ═══════════════════════════════════════════════════════════════════════════════

def can_delegate_role(granting_role: str, target_role: str) -> bool:
    """
    A user can only delegate a role below their own level.
    Admins cannot create other admins; only superadmins can.
    """
    return role_level(granting_role) > role_level(target_role)


def assert_can_delegate(granting_role: str, target_role: str) -> None:
    if not can_delegate_role(granting_role, target_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You cannot assign the '{target_role}' role",
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  PERMISSION SUMMARY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def permission_summary(role: str, tier: str = SubscriptionTier.FREE) -> dict:
    """
    Return a full permission map for a role + tier combination.
    Useful for the frontend to conditionally show/hide features.
    """
    result: dict[str, bool] = {}
    for perm in PERMISSION_MATRIX:
        role_ok = has_permission(role, perm)
        tier_ok = has_tier_access(tier, perm)
        result[perm] = role_ok and tier_ok
    return {
        "role": role,
        "tier": tier,
        "permissions": result,
        "granted_count": sum(result.values()),
        "total_count": len(result),
    }
