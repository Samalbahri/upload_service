from datetime import timedelta
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import UploadRateLimit


MAX_ATTEMPTS = 10
WINDOW_SECONDS = 60

def admit_upload(db: Session, user_id: str) -> int:
    db.execute(
        insert(UploadRateLimit)
        .values(
            user_id=user_id,
            window_start=func.clock_timestamp(),
            attempts=0,
        )
        .on_conflict_do_nothing(
            index_elements=[UploadRateLimit.user_id],
        )
    )
    limit = db.execute(
        select(UploadRateLimit)
        .where(UploadRateLimit.user_id == user_id)
        .with_for_update()
    ).scalar_one()

    now = db.scalar(select(func.clock_timestamp()))
    window_end = limit.window_start + timedelta(seconds=WINDOW_SECONDS)
    if now >= window_end:
        limit.window_start = now
        limit.attempts = 1
    elif limit.attempts < MAX_ATTEMPTS:
        limit.attempts += 1
    else:
        return max(1, ceil((window_end - now).total_seconds()))

    db.flush()
    return 0
