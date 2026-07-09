"""Authentication endpoints: register, login, refresh, logout."""
import threading

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import (
    _revoked_refresh_tokens,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_token_payload,
    hash_password,
    revoke_access_token,
    verify_password,
)
from ..database import get_db
from ..errors import AppError
from ..models import Organization, User
from ..schemas import LoginRequest, RefreshRequest, RegisterRequest

router = APIRouter(prefix="/auth", tags=["auth"])

# BUGFIX (rule 8, concurrency): the single-use refresh check is a check-then-act
# on shared state (_revoked_refresh_tokens). Without a lock, concurrent refreshes
# of the same token can all pass the membership test before any records the jti,
# so several succeed instead of exactly one. This lock makes check+record atomic.
_refresh_lock = threading.Lock()


@router.post("/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.name == payload.org_name).first()
    role = "admin" if org is None else "member"
    if org is None:
        org = Organization(name=payload.org_name)
        db.add(org)
        try:
            db.commit()
        except IntegrityError:
            # BUGFIX (rule 15/16): two concurrent registrations for the same
            # brand-new org race on the unique org name. The loser rolls back,
            # re-reads the winner's org and joins it as a member instead of 500.
            db.rollback()
            org = (
                db.query(Organization)
                .filter(Organization.name == payload.org_name)
                .first()
            )
            role = "member"
        else:
            db.refresh(org)

    existing = (
        db.query(User)
        .filter(User.org_id == org.id, User.username == payload.username)
        .first()
    )
    if existing is not None:
        # BUGFIX (rule 15): a duplicate username must be rejected with 409, not
        # silently return the existing user.
        raise AppError(409, "USERNAME_TAKEN", "Username already taken in this organization")

    user = User(
        org_id=org.id,
        username=payload.username,
        hashed_password=hash_password(payload.password),
        role=role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # BUGFIX (rule 15/16): concurrent identical registrations pass the
        # existence check together; the unique (org_id, username) constraint
        # turns the loser's insert into 409 rather than an unhandled 500.
        db.rollback()
        raise AppError(409, "USERNAME_TAKEN", "Username already taken in this organization")
    db.refresh(user)
    return {
        "user_id": user.id,
        "org_id": org.id,
        "username": user.username,
        "role": user.role,
    }


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.name == payload.org_name).first()
    user = None
    if org is not None:
        user = (
            db.query(User)
            .filter(User.org_id == org.id, User.username == payload.username)
            .first()
        )
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid username or password")
    return {
        "access_token": create_access_token(user),
        "refresh_token": create_refresh_token(user),
        "token_type": "bearer",
    }


@router.post("/refresh")
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    data = decode_token(payload.refresh_token)
    if data.get("type") != "refresh":
        raise AppError(401, "UNAUTHORIZED", "Wrong token type")
    # BUGFIX (rule 8): refresh tokens are single-use. Atomically check-and-burn
    # the jti under a lock so concurrent reuse of the same token yields exactly
    # one success; the rest get 401. Burning before the user lookup is safe: an
    # unknown user means the token is invalid anyway.
    with _refresh_lock:
        if data.get("jti") in _revoked_refresh_tokens:
            raise AppError(401, "UNAUTHORIZED", "Refresh token has already been used")
        _revoked_refresh_tokens.add(data["jti"])
    user = db.query(User).filter(User.id == int(data["sub"])).first()
    if user is None:
        raise AppError(401, "UNAUTHORIZED", "Unknown user")
    return {
        "access_token": create_access_token(user),
        "refresh_token": create_refresh_token(user),
        "token_type": "bearer",
    }


@router.post("/logout")
def logout(payload: dict = Depends(get_token_payload)):
    revoke_access_token(payload)
    return {"status": "ok"}
