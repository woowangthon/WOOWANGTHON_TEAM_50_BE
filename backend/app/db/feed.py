"""노출·반응 저장소."""
from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from supabase import Client

from app.db.users import Row
from app.recommend.rank import Recommendation

RecordResult = Literal["recorded", "duplicate", "conflict"]


class FeedRepo:
    def __init__(self, client: Client):
        self._db = client

    def list_impressions(self, user_id: UUID) -> list[Row]:
        """오래된 순. 각 행의 reactions가 비어 있으면 아직 반응하지 않은 노출이다."""
        return (
            self._db.table("impressions")
            .select("id, card_id, source, reactions(id)")
            .eq("user_id", str(user_id))
            .order("created_at")
            .execute()
            .data
        )

    def create_impressions(self, user_id: UUID, recs: Sequence[Recommendation]) -> list[Row]:
        if not recs:
            return []
        rows = [
            {"user_id": str(user_id), "card_id": r.card_id, "source": r.source, "score": r.score}
            for r in recs
        ]
        return self._db.table("impressions").insert(rows).execute().data

    def get_impression(self, impression_id: UUID, user_id: UUID) -> Row | None:
        """다른 사용자의 노출은 없는 것으로 취급한다."""
        rows = (
            self._db.table("impressions")
            .select("id, card_id")
            .eq("id", str(impression_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None

    def record_reaction(
        self,
        impression_id: UUID,
        user_id: UUID,
        card_id: str,
        reaction: str,
        option_id: str | None,
        vector: list[float],
        expected_version: int | None,
    ) -> RecordResult:
        return (
            self._db.rpc(
                "record_reaction",
                {
                    "p_impression_id": str(impression_id),
                    "p_user_id": str(user_id),
                    "p_card_id": card_id,
                    "p_reaction": reaction,
                    "p_option_id": option_id,
                    "p_vector": vector,
                    "p_expected_version": expected_version,
                },
            )
            .execute()
            .data
        )

    def list_reactions(self, user_id: UUID) -> list[Row]:
        """최신 순. 저장 목록·리포트용."""
        return (
            self._db.table("reactions")
            .select("card_id, reaction, created_at")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .execute()
            .data
        )
