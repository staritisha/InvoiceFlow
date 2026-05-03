from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    role: str
    is_active: bool

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str

class CustomerCreate(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    address: str | None = None


class CustomerResponse(BaseModel):
    id: int
    name: str
    email: str | None
    phone: str | None
    address: str | None
    user_id: int

    class Config:
        from_attributes = True

from datetime import datetime


class InvoiceCreate(BaseModel):
    invoice_number: str
    customer_id: int
    due_date: datetime | None = None
    status: str = "draft"
    total_amount: int
    notes: str | None = None


class InvoiceResponse(BaseModel):
    id: int
    invoice_number: str
    customer_id: int
    user_id: int
    issue_date: datetime | None
    due_date: datetime | None
    status: str
    total_amount: int
    notes: str | None

    class Config:
        from_attributes = True


class InvoiceStatusUpdate(BaseModel):
    status: str
class ReminderResponse(BaseModel):
    invoice_id: int
    customer_email: str | None
    subject: str
    message: str

class RecurringBillingCreate(BaseModel):
    customer_id: int
    title: str
    amount: int
    frequency: str
    next_billing_date: datetime
    is_active: bool = True


class RecurringBillingResponse(BaseModel):
    id: int
    customer_id: int
    user_id: int
    title: str
    amount: int
    frequency: str
    next_billing_date: datetime
    is_active: bool

    class Config:
        from_attributes = True    

class InvoiceItemCreate(BaseModel):
    invoice_id: int
    description: str
    quantity: int
    unit_price: int


class InvoiceItemResponse(BaseModel):
    id: int
    invoice_id: int
    description: str
    quantity: int
    unit_price: int
    total_price: int

    class Config:
        from_attributes = True


class AIFollowupResponse(BaseModel):
    invoice_id: int
    tone: str
    subject: str
    message: str