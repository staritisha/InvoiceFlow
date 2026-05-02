from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "InvoiceFlow API"
    debug: bool = True

settings = Settings()