from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models
from app.api.deps import get_db
from app.auth import get_current_user
from app.schemas import CustomerCreate, CustomerResponse

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.post("", response_model=CustomerResponse)
def create_customer(
    customer: CustomerCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    new_customer = models.Customer(
        name=customer.name,
        email=customer.email,
        phone=customer.phone,
        address=customer.address,
        user_id=current_user.id,
    )
    db.add(new_customer)
    db.commit()
    db.refresh(new_customer)
    return new_customer


@router.get("", response_model=list[CustomerResponse])
def get_customers(db: Session = Depends(get_db)):
    return db.query(models.Customer).all()


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.put("/{customer_id}", response_model=CustomerResponse)
def update_customer(customer_id: int, customer: CustomerCreate, db: Session = Depends(get_db)):
    db_customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not db_customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    db_customer.name = customer.name
    db_customer.email = customer.email
    db_customer.phone = customer.phone
    db_customer.address = customer.address
    db.commit()
    db.refresh(db_customer)
    return db_customer


@router.delete("/{customer_id}")
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    db.delete(customer)
    db.commit()
    return {"message": "Customer deleted successfully"}
