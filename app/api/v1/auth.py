"""
Auth router — user/company membership management.

Endpoints:
  GET    /auth/companies          — list companies for current user
  POST   /auth/companies/join     — join a company by NIT
  DELETE /auth/companies/{nit}    — leave a company
"""

import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
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


# ─── Endpoints ───────────────────────────────────────────────────


@router.get("/companies", response_model=List[UserCompanyResponse])
def list_user_companies(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> List[UserCompany]:
    """Return all companies the current user belongs to."""
    return (
        db.query(UserCompany).filter(UserCompany.user_id == str(current_user.id)).all()
    )


@router.post(
    "/companies/join",
    response_model=UserCompanyResponse,
    status_code=status.HTTP_201_CREATED,
)
def join_company(
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

    existing = (
        db.query(UserCompany)
        .filter(
            UserCompany.user_id == str(current_user.id),
            UserCompany.company_nit == body.nit,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User is already a member of company '{body.nit}'.",
        )

    membership = UserCompany(user_id=str(current_user.id), company_nit=body.nit)
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
    membership = (
        db.query(UserCompany)
        .filter(
            UserCompany.user_id == str(current_user.id),
            UserCompany.company_nit == nit,
        )
        .first()
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Membership for NIT '{nit}' not found.",
        )

    db.delete(membership)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
