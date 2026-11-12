
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )

    # Global content identity
    sha256: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )

    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    storage_key: Mapped[str] = mapped_column(
        String(500),
        unique=True,
        nullable=False,
    )

    thumbnail_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'pending'"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "size_bytes >= 0",
            name="ck_files_size_nonnegative",
        ),
    )


class UserFile(Base):
    __tablename__ = "user_files"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )

    user_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id"),
        nullable=False,
        index=True,
    )

    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Quota(Base):
    __tablename__ = "quota"

    user_id: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
    )

    limit_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    used_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )

    reserved_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )

    __table_args__ = (
        CheckConstraint(
            "limit_bytes >= 0",
            name="ck_quota_limit_nonnegative",
        ),
        CheckConstraint(
            "used_bytes >= 0",
            name="ck_quota_used_nonnegative",
        ),
        CheckConstraint(
            "reserved_bytes >= 0",
            name="ck_quota_reserved_nonnegative",
        ),
        CheckConstraint(
            "used_bytes + reserved_bytes <= limit_bytes",
            name="ck_quota_within_limit",
        ),
    )
