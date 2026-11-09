"""Supabase JWT 검증. 프로젝트의 공개 키(JWKS)로 서명을 확인한다."""
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError

ALGORITHMS = ["ES256"]        # 대칭키(HS256)를 허용하면 알고리즘 혼동 공격에 노출된다
AUDIENCE = "authenticated"    # 로그인한 사용자 토큰의 aud


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str | None


class InvalidToken(Exception):
    pass


class AuthUnavailable(Exception):
    """공개 키를 가져오지 못함. 토큰 문제가 아니므로 401로 답하지 않는다."""


class TokenVerifier:
    def __init__(self, supabase_url: str, jwks: PyJWKClient | None = None):
        base = supabase_url.rstrip("/") + "/auth/v1"
        self._issuer = base
        self._jwks = jwks or PyJWKClient(f"{base}/.well-known/jwks.json", timeout=5)

    def verify(self, token: str) -> CurrentUser:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=AUDIENCE,
                issuer=self._issuer,
                options={"require": ["exp", "sub"]},
            )
            return CurrentUser(id=UUID(claims["sub"]), email=claims.get("email") or None)
        except PyJWKClientConnectionError as e:
            raise AuthUnavailable from e
        except (jwt.PyJWTError, ValueError) as e:
            raise InvalidToken from e


_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "invalid_token", headers={"WWW-Authenticate": "Bearer"}
    )
    if credentials is None:
        raise unauthorized
    try:
        return request.app.state.verifier.verify(credentials.credentials)
    except InvalidToken:
        raise unauthorized from None
    except AuthUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth_unavailable") from None
