from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

import models
from database import get_db
from security import token_hash


SESSION_COOKIE = "erp_session"
CSRF_COOKIE = "erp_csrf"


def current_user(request: Request, db: Session = Depends(get_db)) -> models.User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    session = db.query(models.UserSession).filter(models.UserSession.token_hash == token_hash(token)).first()
    now = datetime.now(timezone.utc)
    if not session or session.expires_at <= now or not session.user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    session.last_seen_at = now
    db.commit()
    return session.user


def admin_user(user: models.User = Depends(current_user)) -> models.User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")
    return user


def organization_ids(user: models.User) -> set[int]:
    return {membership.organization_id for membership in user.memberships}


def require_project(db: Session, user: models.User, project_id: int) -> models.Project:
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if user.role != "admin" and project.organization_id not in organization_ids(user):
        raise HTTPException(status_code=404, detail="Project not found")
    return project
