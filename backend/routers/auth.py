"""
routers/auth.py - login + "who am I" + small directories used by dropdowns.

A "router" is a group of related URLs (endpoints). main.py plugs all routers
together into one app.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend import audit
from backend.database import get_db
from backend.models import Organization, User
from backend.schemas import LoginRequest
from backend.security import create_access_token, get_current_user, verify_password
from backend.serializers import org_out, user_out

router = APIRouter(tags=["auth"])


@router.post("/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username.strip().lower()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        # Same message for both cases so attackers can't learn which usernames exist.
        raise HTTPException(401, "Wrong username or password")
    if user.status != "active":
        raise HTTPException(403, "This account is disabled")
    audit.log(db, user, "login", "user", user.id, user.patient_id)
    db.commit()
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": user_out(user)}


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    return user_out(user)


@router.get("/directory/doctors")
def list_doctors(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Doctors (for 'grant access to…'). Only names + clinic are shared."""
    docs = db.query(User).filter(User.role == "doctor", User.status == "active").all()
    return [{"id": d.id, "full_name": d.full_name,
             "organization_name": d.organization.name if d.organization else ""} for d in docs]


@router.get("/directory/pharmacies")
def list_pharmacies(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [org_out(o) for o in db.query(Organization).filter(Organization.type == "pharmacy").all()]
