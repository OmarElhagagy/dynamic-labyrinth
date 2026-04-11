from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ORCHESTRATOR_URL: str = "http://orchestrator:8080"
    HMAC_SECRET: str = "your-secret-key"
    DB_PATH: str = "cerebrum.db"
    LOG_LEVEL: str = "INFO"

settings = Settings()