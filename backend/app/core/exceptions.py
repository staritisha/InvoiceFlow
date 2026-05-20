import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from app.middleware.logging import get_request_id

logger = logging.getLogger("invoiceflow")


def _error_response(request: Request, message: str, status_code: int = 500):
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "message": message,
            "request_id": get_request_id(request),
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return _error_response(request, "Validation error", 422)

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _error_response(request, detail, exc.status_code)

    @app.exception_handler(SQLAlchemyError)
    async def db_handler(request: Request, exc: SQLAlchemyError):
        logger.exception("Database error", extra={"request_id": get_request_id(request)})
        return _error_response(request, "Database error", 500)

    @app.exception_handler(Exception)
    async def generic_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error", extra={"request_id": get_request_id(request)})
        return _error_response(request, "Internal server error", 500)
