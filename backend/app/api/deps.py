"""라우터 공용 의존성."""
from fastapi import Request

from app.content import ContentStore
from app.db.feed import FeedRepo
from app.db.users import UserRepo
from app.llm.report_writer import ReportWriter


def get_content(request: Request) -> ContentStore:
    return request.app.state.content


def get_users(request: Request) -> UserRepo:
    return request.app.state.users


def get_feed(request: Request) -> FeedRepo:
    return request.app.state.feed


def get_report_writer(request: Request) -> ReportWriter | None:
    """GEMINI_API_KEY 가 없으면 None."""
    return getattr(request.app.state, "report_writer", None)
