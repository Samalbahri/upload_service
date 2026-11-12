from sqlalchemy.orm import Session
from app.models import Quota


from sqlalchemy import update


def charge_quota(
    db: Session,
    user_id: str,
    size_bytes: int,
) -> bool:
    result = db.execute(
        update(Quota)
        .where(
            Quota.user_id == user_id,
            Quota.used_bytes
            + Quota.reserved_bytes
            + size_bytes
            <= Quota.limit_bytes,
        )
        .values(
            used_bytes=Quota.used_bytes + size_bytes
        )
    )

    return result.rowcount == 1



def get_quota(db: Session, user_id: str) -> Quota | None:
    return db.get(Quota, user_id)