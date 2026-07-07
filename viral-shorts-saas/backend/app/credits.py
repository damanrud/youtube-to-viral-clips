from sqlalchemy.orm import Session
from app.models import User, CreditTransaction
from fastapi import HTTPException

def check_credits(db: Session, user_id, required: int) -> bool:
    user = db.query(User).filter(User.id == user_id).first()
    return user and user.credits >= required

def deduct_credits(db: Session, user_id, amount: int, type: str, description: str):
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.credits < amount:
        raise HTTPException(400, "Not enough credits")
    user.credits -= amount
    db.add(CreditTransaction(user_id=user_id, amount=-amount, type=type, description=description))
    db.commit()
    db.refresh(user)

def add_credits(db: Session, user_id, amount: int, type: str, description: str):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    user.credits += amount
    db.add(CreditTransaction(user_id=user_id, amount=amount, type=type, description=description))
    db.commit()
    db.refresh(user)