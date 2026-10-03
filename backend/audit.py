"""
audit.py - tiny helpers used by almost every endpoint.

  log()    -> writes an AuditEvent ("Dr Ayesha viewed timeline of patient 3")
  notify() -> creates a Notification for a user (bell icon in the UI)

They only ADD rows to the session. The calling endpoint decides when to
`db.commit()` so that the action and its audit record are saved together.
"""
from typing import Optional

from sqlalchemy.orm import Session

from backend.models import AuditEvent, Notification, User


def log(
    db: Session,
    user: Optional[User],
    action: str,
    entity_type: str = "",
    entity_id="",
    patient_id: Optional[int] = None,
    detail: str = "",
) -> None:
    db.add(
        AuditEvent(
            actor_id=user.id if user else None,
            actor_name=user.full_name if user else "system",
            actor_role=user.role if user else "",
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            patient_id=patient_id,
            detail=detail,
        )
    )


def notify(db: Session, user_id: Optional[int], title: str, message: str = "") -> None:
    if user_id:
        db.add(Notification(user_id=user_id, title=title, message=message))
