"""Auth endpoints: register and login (access-token-only flow for M1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api import auth
from app.api.deps import get_repo
from app.api.repo import Repository
from app.api.schemas import TokenPair, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: UserCreate, repo: Repository = Depends(get_repo)) -> UserOut:
    if repo.get_user_by_email(body.email) is not None:
        raise HTTPException(status_code=409, detail="Email already registered")
    salt = auth.new_salt()
    user = repo.create_user(body.email, auth.hash_password(body.password, salt), salt)
    return UserOut(id=user.id, email=user.email)


@router.post("/login", response_model=TokenPair)
def login(body: UserCreate, repo: Repository = Depends(get_repo)) -> TokenPair:
    user = repo.get_user_by_email(body.email)
    if user is None or not auth.verify_password(
        body.password, user.salt, user.password_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenPair(access_token=auth.create_token(user.id))
