from fastapi import FastAPI

app = FastAPI(title="InvoiceFlow API")

@app.get("/")
def root():
    return {"message": "InvoiceFlow running"}

@app.get("/health")
def health():
    return {"status": "ok"}