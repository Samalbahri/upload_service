
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Find the backend directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Load variables from backend/.env
load_dotenv(BASE_DIR / ".env")

# Read the database connection string
DATABASE_URL = os.environ["DATABASE_URL"]


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)
