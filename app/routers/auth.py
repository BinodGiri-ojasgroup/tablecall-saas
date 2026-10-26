from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import LoginAttempt, User, get_db, utcnow
from ..schemas import LoginIn, PasswordChangeIn
from ..security import (SESSION_COOKIE, audit, burn_password_check, current_user, hash_password, issue_token,
                        key_for_login, verify_password, window_start)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_payload(user: User) -> dict:
    return {"id": user.id, "email": user.email, "role": user.role, "restaurant_id": user.restaurant_id}


def _set_cookie(response: Response, user: User) -> None:
    response.set_cookie(SESSION_COOKIE, issue_token(user), httponly=True, secure=settings.cookie_secure,
                        samesite="lax", max_age=settings.session_hours * 3600, path="/")


@router.post("/login")
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    key = key_for_login(request, data.email)
    failures = db.scalar(select(func.count(LoginAttempt.id)).where(
        LoginAttempt.key == key, LoginAttempt.created_at >= window_start(settings.login_failure_window_minutes))) or 0
    if failures >= settings.login_failure_limit:
        raise HTTPException(status_code=429, detail="Too many failed sign-in attempts. Try again in 15 minutes.")

    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if user is None:
        burn_password_check(data.password)
    if not user or not user.is_active or not verify_password(data.password, user.password_hash):
        db.add(LoginAttempt(key=key, created_at=utcnow()))
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    db.execute(delete(LoginAttempt).where(LoginAttempt.key == key))
    audit(db, user, "auth.login")
    db.commit()
    _set_cookie(response, user)
    return _user_payload(user)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _user_payload(user)


@router.post("/password")
def change_password(data: PasswordChangeIn, response: Response, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if data.current_password == data.new_password:
        raise HTTPException(status_code=400, detail="Choose a password different from the current one.")
    user.password_hash = hash_password(data.new_password)
    audit(db, user, "auth.password_changed")
    db.commit()
    _set_cookie(response, user)  # keep this device signed in; all other sessions are invalidated
    return {"ok": True, "message": "Password updated. Other devices have been signed out."}
