

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "InvoiceFlow API"
    debug: bool = True
    database_url: str

    # Email config
    email_host: str | None = None
    email_port: int | None = None
    email_username: str | None = None
    email_password: str | None = None
    email_from: str | None = None

    # 👉 ADD THIS LINE
    openai_api_key: str | None = None

    class Config:
        env_file = ".env"


settings = Settings()