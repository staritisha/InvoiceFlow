from fastapi import APIRouter

from app.api.v1 import (
    auth,
    customers,
    dashboard,
    exports,
    invoice_items,
    invoices,
    platform,
    recurring,
    scheduler,
)

api_router = APIRouter()
api_router.include_router(platform.router)
api_router.include_router(auth.router)
api_router.include_router(customers.router)
api_router.include_router(invoices.router)
api_router.include_router(invoice_items.router)
api_router.include_router(recurring.router)
api_router.include_router(dashboard.router)
api_router.include_router(exports.router)
api_router.include_router(scheduler.router)
