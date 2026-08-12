from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    database_url: str = Field(default="postgresql+psycopg://anaslari@localhost:5432/erp_agent", env="DATABASE_URL")
    encryption_key: str = Field(default="", env="ENCRYPTION_KEY")
    
    class Config:
        env_file = ".env"

settings = Settings()
