from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from config import settings

# Wait, we decided on PostgreSQL.
# The URL should look like postgresql+psycopg://user:password@host:port/dbname
SQLALCHEMY_DATABASE_URL = settings.database_url

# For local development we can just use psycopg directly
engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
