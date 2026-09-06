"""Auth dependency: session token from Authorization header; role checks."""
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from . import services
from .db import get_db
from .models import Role, User


def current_user(db: Session = Depends(get_db), authorization: str = Header(default="")) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    user = services.get_user_by_token(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return user


def require_role(*roles: Role):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in [r.value for r in roles]:
            raise HTTPException(status_code=403, detail="Insufficient permissions.")
        return user
    return dep
