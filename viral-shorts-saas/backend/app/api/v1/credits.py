from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, CreditTransaction
from app.schemas import CreditTransactionResponse
from app.dependencies import get_current_user

router = APIRouter(prefix="/credits", tags=["credits"])

@router.get("/balance")
def get_balance(user: User = Depends(get_current_user)):
    return {"credits": user.credits}

@router.get("/history", response_model=list[CreditTransactionResponse])
def get_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    txs = db.query(CreditTransaction).filter(CreditTransaction.user_id == user.id).order_by(CreditTransaction.created_at.desc()).all()
    return txs

@router.post("/purchase")
def purchase_credits(amount: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Заглушка для Stripe
    return {"message": f"Purchase {amount} credits (Stripe integration placeholder)"}