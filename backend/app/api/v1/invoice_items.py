from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models
from app.api.deps import get_db
from app.schemas import InvoiceItemCreate, InvoiceItemResponse

router = APIRouter(prefix="/invoice-items", tags=["Invoices"])


@router.post("", response_model=InvoiceItemResponse)
def create_invoice_item(item: InvoiceItemCreate, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == item.invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    total_price = item.quantity * item.unit_price
    new_item = models.InvoiceItem(
        invoice_id=item.invoice_id,
        description=item.description,
        quantity=item.quantity,
        unit_price=item.unit_price,
        total_price=total_price,
    )
    db.add(new_item)
    invoice.total_amount += total_price
    db.commit()
    db.refresh(new_item)
    return new_item


@router.get("", response_model=list[InvoiceItemResponse])
def get_invoice_items(db: Session = Depends(get_db)):
    return db.query(models.InvoiceItem).all()


@router.get("/{item_id}", response_model=InvoiceItemResponse)
def get_invoice_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(models.InvoiceItem).filter(models.InvoiceItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Invoice item not found")
    return item


@router.put("/{item_id}", response_model=InvoiceItemResponse)
def update_invoice_item(item_id: int, item: InvoiceItemCreate, db: Session = Depends(get_db)):
    db_item = db.query(models.InvoiceItem).filter(models.InvoiceItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Invoice item not found")
    invoice = db.query(models.Invoice).filter(models.Invoice.id == db_item.invoice_id).first()
    old_total = db_item.total_price
    new_total = item.quantity * item.unit_price
    db_item.invoice_id = item.invoice_id
    db_item.description = item.description
    db_item.quantity = item.quantity
    db_item.unit_price = item.unit_price
    db_item.total_price = new_total
    if invoice:
        invoice.total_amount = invoice.total_amount - old_total + new_total
    db.commit()
    db.refresh(db_item)
    return db_item


@router.delete("/{item_id}")
def delete_invoice_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(models.InvoiceItem).filter(models.InvoiceItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Invoice item not found")
    invoice = db.query(models.Invoice).filter(models.Invoice.id == item.invoice_id).first()
    if invoice:
        invoice.total_amount -= item.total_price
    db.delete(item)
    db.commit()
    return {"message": "Invoice item deleted successfully"}
