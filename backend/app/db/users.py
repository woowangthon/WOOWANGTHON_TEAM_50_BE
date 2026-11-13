"""프로필·성향 벡터 저장소."""
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from postgrest.exceptions import APIError
from supabase import Client

UNIQUE_VIOLATION = "23505"

Row = dict[str, Any]


class ProfileExists(Exception):
    pass


class UserRepo:
    def __init__(self, client: Client):
        self._db = client

    def get_profile(self, user_id: UUID) -> Row | None:
        rows = self._db.table("profiles").select("*").eq("user_id", str(user_id)).limit(1).execute().data
        return rows[0] if rows else None

    def create_profile(self, user_id: UUID, fields: Row) -> Row:
        try:
            return self._db.table("profiles").insert({"user_id": str(user_id), **fields}).execute().data[0]
        except APIError as e:
            if e.code == UNIQUE_VIOLATION:
                raise ProfileExists from e
            raise

    def update_profile(self, user_id: UUID, fields: Row) -> Row | None:
        values = {**fields, "updated_at": datetime.now(UTC).isoformat()}
        rows = self._db.table("profiles").update(values).eq("user_id", str(user_id)).execute().data
        return rows[0] if rows else None

    def get_vector(self, user_id: UUID) -> Row | None:
        """아직 반응이 없는 사용자는 행이 없다 (None)."""
        rows = (
            self._db.table("user_vectors")
            .select("vector, n_reactions, version")
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None

    def get_interest_survey(self, user_id: UUID) -> Row | None:
        """설문을 아직 안 했으면 None. [{version, answers, created_at}]"""
        rows = (
            self._db.table("interest_surveys")
            .select("version, answers, created_at")
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None

    def record_interest_survey(
        self,
        user_id: UUID,
        version: int,
        answers: dict[str, str],
        vector: list[float],
        expected_version: int | None,
    ) -> str:
        """'recorded' | 'duplicate' | 'conflict'. 마이그레이션 20261111010000_interest_survey 참고."""
        return (
            self._db.rpc(
                "record_interest_survey",
                {
                    "p_user_id": str(user_id),
                    "p_version": version,
                    "p_answers": answers,
                    "p_vector": vector,
                    "p_expected_version": expected_version,
                },
            )
            .execute()
            .data
        )
