"""
routers/admin.py - platform admin + analytics + notifications.

Admins manage accounts and see AGGREGATE numbers and the audit trail.
They intentionally cannot open patient clinical records.
"""
from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import audit
from backend.database import get_db, utcnow
from backend.models import (
    AuditEvent, ConflictAlert, Dispensing, Encounter, LabResult, Notification, Organization,
    Patient, Prescription, User,
)
from backend.schemas import UserCreate, UserStatus
from backend.security import get_current_user, hash_password, require_roles
from backend.serializers import notification_out, org_out, user_out

router = APIRouter(tags=["admin"])


@router.get("/admin/analytics")
def analytics(db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "doctor"))):
    """Platform-level numbers (no patient names)."""
    rx_by_status = dict(db.query(Prescription.status, func.count()).group_by(Prescription.status).all())
    alerts_by_sev = dict(db.query(ConflictAlert.severity, func.count()).group_by(ConflictAlert.severity).all())
    alerts_by_kind = dict(db.query(ConflictAlert.kind, func.count()).group_by(ConflictAlert.kind).all())

    # average minutes from "sent" to "verified"
    verified = db.query(Prescription).filter(Prescription.verified_at.isnot(None)).all()
    mins = [(r.verified_at - r.issued_at).total_seconds() / 60 for r in verified]

    # events per day for the last 14 days
    since = utcnow() - timedelta(days=14)
    per_day = Counter()
    for r in db.query(Prescription.issued_at).filter(Prescription.issued_at >= since):
        per_day[r[0].date().isoformat()] += 1

    return {
        "totals": {
            "patients": db.query(Patient).count(), "encounters": db.query(Encounter).count(),
            "prescriptions": db.query(Prescription).count(), "dispensings": db.query(Dispensing).count(),
            "labs": db.query(LabResult).count(), "open_alerts": db.query(ConflictAlert).filter(
                ConflictAlert.status == "open").count(),
            "organizations": db.query(Organization).count(),
        },
        "prescriptions_by_status": rx_by_status,
        "alerts_by_severity": alerts_by_sev,
        "alerts_by_kind": alerts_by_kind,
        "avg_minutes_to_verify": round(sum(mins) / len(mins), 1) if mins else None,
        "prescriptions_per_day": dict(sorted(per_day.items())),
    }


@router.get("/admin/users")
def users(db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    return [user_out(u) for u in db.query(User).order_by(User.role, User.full_name).all()]


@router.get("/admin/organizations")
def orgs(db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    return [org_out(o) for o in db.query(Organization).all()]


@router.post("/admin/users", status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    username = body.username.strip().lower()
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(409, "Username already exists")
    if body.role in ("doctor", "pharmacist") and body.organization_id is None:
        raise HTTPException(422, "Doctors and pharmacists need an organization")
    u = User(username=username, password_hash=hash_password(body.password), full_name=body.full_name,
             role=body.role, organization_id=body.organization_id)
    db.add(u)
    db.flush()
    audit.log(db, admin, "user.create", "user", u.id, None, f"{u.username} ({u.role})")
    db.commit()
    return user_out(u)


@router.patch("/admin/users/{user_id}")
def set_status(user_id: int, body: UserStatus, db: Session = Depends(get_db),
               admin: User = Depends(require_roles("admin"))):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.id == admin.id:
        raise HTTPException(422, "You cannot disable your own account")
    u.status = body.status
    audit.log(db, admin, "user.status", "user", u.id, None, body.status)
    db.commit()
    return user_out(u)


@router.get("/admin/audit")
def audit_log(limit: int = 200, db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    rows = db.query(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(min(limit, 1000)).all()
    return [{"timestamp": r.timestamp.isoformat(), "actor": r.actor_name, "role": r.actor_role,
             "action": r.action, "entity": f"{r.entity_type} {r.entity_id}".strip(), "patient_id": r.patient_id,
             "detail": r.detail} for r in rows]


# ------------------------------------------------------------ notifications --
@router.get("/notifications")
def my_notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Notification).filter(Notification.user_id == user.id) \
        .order_by(Notification.created_at.desc()).limit(50).all()
    return [notification_out(n) for n in rows]


@router.post("/notifications/read-all")
def read_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read.is_(False)) \
        .update({"is_read": True})
    db.commit()
    return {"ok": True}
