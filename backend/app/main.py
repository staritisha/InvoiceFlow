from fastapi import FastAPI
from sqlalchemy import text
from app.database import engine, Base
from app import models

Base.metadata.create_all(bind=engine)

app = FastAPI(title="InvoiceFlow API")

@app.get("/")
def root():
    return {"message": "InvoiceFlow running"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/db-check")
def db_check():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        return {"database": "connected", "result": result.scalar()}
