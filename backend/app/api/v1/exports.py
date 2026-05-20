import csv

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import models
from app.api.deps import get_db

router = APIRouter(prefix="/exports", tags=["Reports"])


@router.get("/invoices-csv")
def export_invoices_csv(db: Session = Depends(get_db)):
    file_path = "app/invoices_export.csv"
    invoices = db.query(models.Invoice).all()
    with open(file_path, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Invoice Number", "Customer ID", "Status", "Total Amount", "Due Date", "Notes"])
        for invoice in invoices:
            writer.writerow(
                [
                    invoice.id,
                    invoice.invoice_number,
                    invoice.customer_id,
                    invoice.status,
                    invoice.total_amount,
                    invoice.due_date,
                    invoice.notes,
                ]
            )
    return FileResponse(file_path, media_type="text/csv", filename="invoices_export.csv")
