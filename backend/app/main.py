"""FastAPI 앱. 시작할 때 설정과 콘텐츠를 검증하고, 실패하면 서버가 뜨지 않는다."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client

from app.api import feed, health, interest_survey, jobs, me, onboarding
from app.content import load_content
from app.core.auth import TokenVerifier
from app.core.config import Settings, get_settings
from app.db.feed import FeedRepo
from app.db.users import UserRepo
from app.jobs import load_job_index
from app.llm.report_writer import create_writer


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service_key = settings.supabase_service_role_key.get_secret_value()
        if not settings.supabase_url or not service_key:
            raise RuntimeError("SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY를 설정하세요 (backend/.env)")
        app.state.content = load_content(reviewed_only=settings.content_reviewed_only)
        app.state.job_index = load_job_index(app.state.content)
        app.state.verifier = TokenVerifier(settings.supabase_url)
        client = create_client(settings.supabase_url, service_key)
        app.state.users = UserRepo(client)
        app.state.feed = FeedRepo(client)
        gemini_key = settings.gemini_api_key.get_secret_value()
        app.state.report_writer = (
            create_writer(gemini_key, settings.gemini_model, settings.gemini_timeout_s) if gemini_key else None
        )
        yield

    app = FastAPI(title="FIND-ME API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(me.router)
    app.include_router(feed.router)
    app.include_router(onboarding.router)
    app.include_router(jobs.router)
    app.include_router(interest_survey.router)
    return app


app = create_app()
