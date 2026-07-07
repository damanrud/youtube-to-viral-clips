from sqlalchemy import Column, String, Integer, DateTime, JSON, ForeignKey, UUID as SA_UUID
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from app.database import Base
import uuid
from datetime import datetime

class User(Base):
    __tablename__ = "users"
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)
    password = Column(String(255), nullable=True)
    google_id = Column(String(255), unique=True, nullable=True)
    full_name = Column(String(255))
    avatar_url = Column(String(500), nullable=True)
    language = Column(String(2), default="ru")
    credits = Column(Integer, default=100)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    source_type = Column(String(20))  # youtube, text
    source_url = Column(String(500), nullable=True)
    text_input = Column(String(5000), nullable=True)
    options = Column(JSON, nullable=True)
    status = Column(String(20), default="pending")
    progress = Column(Integer, default=0)
    result_data = Column(JSON, nullable=True)
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CreditTransaction(Base):
    __tablename__ = "credit_transactions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    amount = Column(Integer)
    type = Column(String(20))
    description = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)