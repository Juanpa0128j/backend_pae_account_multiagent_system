"""
Auth router — user/company membership management.

Endpoints:
  GET    /auth/companies          — list companies for current user
  POST   /auth/companies/join     — join a company by NIT
  DELETE /auth/companies/{nit}    — leave a company

Memberships are keyed on Supabase user_id (UUID) but we also persist
user_email so that re-signups with the same email recover their previous
companies — Supabase issues a new UUID on each signup which would
otherwise orphan all prior memberships.
"""

import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.limiter import limiter
from app.models.database import CompanySettings, UserCompany

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────


class UserCompanyResponse(BaseModel):
    user_id: str
    company_nit: str
    joined_at: datetime

    model_config = {"from_attributes": True}


class JoinCompanyRequest(BaseModel):
    nit: str


# ─── Helpers ─────────────────────────────────────────────────────


def _normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    return email.strip().lower() or None


def _reassociate_memberships(
    db: Session, current_user: CurrentUser
) -> List[UserCompany]:
    """Return the user's memberships, re-linking any email-matched orphans.

    Looks up rows by current user_id OR user_email. For any row matched only
    by email (i.e. a previous signup's user_id), updates user_id and
    user_email to the current values. Commits if anything was rewritten.
    """
    user_id = str(current_user.id)
    email = _normalize_email(current_user.email)

    query = db.query(UserCompany)
    if email:
        query = query.filter(
            or_(UserCompany.user_id == user_id, UserCompany.user_email == email)
        )
    else:
        query = query.filter(UserCompany.user_id == user_id)

    memberships = query.all()

    rewritten = False
    for m in memberships:
        if m.user_id != user_id:
            m.user_id = user_id
            rewritten = True
        if email and m.user_email != email:
            m.user_email = email
            rewritten = True

    if rewritten:
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        for m in memberships:
            db.refresh(m)

    return memberships


# ─── Endpoints ───────────────────────────────────────────────────


@router.get("/companies", response_model=List[UserCompanyResponse])
def list_user_companies(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> List[UserCompany]:
    """Return all companies the current user belongs to.

    Re-associates orphaned rows (matching email but stale user_id) on the fly,
    so a user signing back up with the same email recovers their memberships.
    """
    return _reassociate_memberships(db, current_user)


@router.post(
    "/companies/join",
    response_model=UserCompanyResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("10/minute")
def join_company(
    request: Request,
    body: JoinCompanyRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> UserCompany:
    """Join a company by NIT. 404 if NIT unknown, 409 if already member."""
    company = db.query(CompanySettings).filter(CompanySettings.nit == body.nit).first()
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with NIT '{body.nit}' not found.",
        )

    user_id = str(current_user.id)
    email = _normalize_email(current_user.email)

    # Match either the current user_id OR a row from a prior signup with the
    # same email — both mean "already a member" and should round-trip.
    query = db.query(UserCompany).filter(UserCompany.company_nit == body.nit)
    if email:
        query = query.filter(
            or_(UserCompany.user_id == user_id, UserCompany.user_email == email)
        )
    else:
        query = query.filter(UserCompany.user_id == user_id)
    existing = query.first()

    if existing:
        # Only treat as recovery (return 201) when user_id was stale — i.e. a
        # different prior signup owned this row. A user_id match with a
        # missing email is just a backfill, still 409.
        recovered = existing.user_id != user_id
        if recovered:
            existing.user_id = user_id
        if email and existing.user_email != email:
            existing.user_email = email
        if recovered or (email and existing.user_email == email):
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
            db.refresh(existing)
        if recovered:
            return existing
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User is already a member of company '{body.nit}'.",
        )

    membership = UserCompany(user_id=user_id, company_nit=body.nit, user_email=email)
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return membership


@router.delete("/companies/{nit}", status_code=status.HTTP_204_NO_CONTENT)
def leave_company(
    nit: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    """Leave a company. 404 if membership not found."""
    user_id = str(current_user.id)
    email = _normalize_email(current_user.email)

    query = db.query(UserCompany).filter(UserCompany.company_nit == nit)
    if email:
        query = query.filter(
            or_(UserCompany.user_id == user_id, UserCompany.user_email == email)
        )
    else:
        query = query.filter(UserCompany.user_id == user_id)
    membership = query.first()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Membership for NIT '{nit}' not found.",
        )

    db.delete(membership)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
