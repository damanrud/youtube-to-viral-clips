from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.schemas import UserRegister, UserLogin, GoogleLogin, Token, UserResponse
from app.auth import get_password_hash, verify_password, create_access_token, create_refresh_token, decode_token
from app.credits import add_credits
import requests

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", response_model=UserResponse)
def register(data: UserRegister, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(400, "Email already registered")
    hashed = get_password_hash(data.password)
    user = User(email=data.email, password=hashed, full_name=data.full_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    add_credits(db, user.id, 100, "registration", "Бонус за регистрацию")
    return user

@router.post("/login", response_model=Token)
def login(data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if not user or not verify_password(data.password, user.password):
        raise HTTPException(401, "Invalid credentials")
    access = create_access_token({"sub": str(user.id)})
    refresh = create_refresh_token({"sub": str(user.id)})
    return {"access_token": access, "refresh_token": refresh}

@router.post("/google", response_model=Token)
def google_login(data: GoogleLogin, db: Session = Depends(get_db)):
    resp = requests.get(f"https://oauth2.googleapis.com/tokeninfo?id_token={data.id_token}")
    if resp.status_code != 200:
        raise HTTPException(401, "Invalid Google token")
    info = resp.json()
    email = info.get("email")
    google_id = info.get("sub")
    if not email or not google_id:
        raise HTTPException(401, "Invalid token data")
    user = db.query(User).filter(User.google_id == google_id).first()
    if not user:
        user = User(email=email, google_id=google_id, full_name=info.get("name", "User"))
        db.add(user)
        db.commit()
        db.refresh(user)
        add_credits(db, user.id, 100, "registration", "Бонус за регистрацию через Google")
    access = create_access_token({"sub": str(user.id)})
    refresh = create_refresh_token({"sub": str(user.id)})
    return {"access_token": access, "refresh_token": refresh}

@router.post("/refresh", response_model=Token)
def refresh(refresh_token: str):
    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(401, "Invalid refresh token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "Invalid token")
    access = create_access_token({"sub": user_id})
    return {"access_token": access, "refresh_token": refresh_token}