from pydantic import BaseModel, EmailStr
from uuid import UUID
from datetime import datetime
from typing import Optional, Dict, Any, List

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    full_name: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class GoogleLogin(BaseModel):
    id_token: str

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    avatar_url: Optional[str] = None
    language: str
    credits: int
    created_at: datetime

class SubmitRequest(BaseModel):
    source_type: str  # youtube, text
    source_url: Optional[str] = None
    text_input: Optional[str] = None
    options: Optional[Dict[str, Any]] = {}

class JobResponse(BaseModel):
    job_id: UUID
    status: str
    progress: int
    result_data: Optional[Dict] = None
    error: Optional[str] = None
    created_at: datetime

class CreditTransactionResponse(BaseModel):
    id: int
    amount: int
    type: str
    description: str
    created_at: datetime