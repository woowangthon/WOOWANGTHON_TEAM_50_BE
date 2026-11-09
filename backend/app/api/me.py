from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_content, get_users
from app.content import N_DIM, ContentStore
from app.core.auth import CurrentUser, get_current_user
from app.db.users import ProfileExists, Row, UserRepo
from app.schemas.me import MeResponse, ProfileCreate, ProfileUpdate, Trait

router = APIRouter(prefix="/me", tags=["me"])

User = Annotated[CurrentUser, Depends(get_current_user)]
Users = Annotated[UserRepo, Depends(get_users)]
Content = Annotated[ContentStore, Depends(get_content)]


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "profile_not_found")


def _me(user: CurrentUser, profile: Row, users: UserRepo, content: ContentStore) -> MeResponse:
    row = users.get_vector(user.id)
    vector = row["vector"] if row else [0.0] * N_DIM
    return MeResponse(
        user_id=user.id,
        email=user.email,
        nickname=profile["nickname"],
        grade=profile["grade"],
        created_at=profile["created_at"],
        updated_at=profile["updated_at"],
        n_reactions=row["n_reactions"] if row else 0,
        traits=[
            Trait(code=a.code, label=a.label, group=a.group, score=vector[a.idx])
            for a in content.attributes
        ],
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_me(body: ProfileCreate, user: User, users: Users, content: Content) -> MeResponse:
    try:
        profile = users.create_profile(user.id, body.model_dump())
    except ProfileExists:
        raise HTTPException(status.HTTP_409_CONFLICT, "profile_exists") from None
    return _me(user, profile, users, content)


@router.get("")
def read_me(user: User, users: Users, content: Content) -> MeResponse:
    profile = users.get_profile(user.id)
    if profile is None:
        raise _not_found()
    return _me(user, profile, users, content)


@router.patch("")
def update_me(body: ProfileUpdate, user: User, users: Users, content: Content) -> MeResponse:
    fields = body.model_dump(exclude_unset=True)
    profile = users.update_profile(user.id, fields) if fields else users.get_profile(user.id)
    if profile is None:
        raise _not_found()
    return _me(user, profile, users, content)
