from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import campaigns, credits, moderation, profiles, public, workflow
from app.core.config import get_settings
from app.core.rate_limit import RateLimitMiddleware
from app.services.common import DomainError


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Private workflow API for the TestExchange testing community.",
    )
    application.add_middleware(RateLimitMiddleware, settings=settings)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @application.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    application.include_router(public.router)
    application.include_router(profiles.router, prefix=settings.api_prefix)
    application.include_router(campaigns.router, prefix=settings.api_prefix)
    application.include_router(workflow.router, prefix=settings.api_prefix)
    application.include_router(credits.router, prefix=settings.api_prefix)
    application.include_router(moderation.router, prefix=settings.api_prefix)
    return application


app = create_app()
