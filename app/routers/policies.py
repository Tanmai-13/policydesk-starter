
"""Policies API."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.db import get_session
from app.models import (
    Policy,
    PolicyCreate,
    PolicyRead,
    PolicyStatus,
    PolicyStatusUpdate,
    ProductCode,
    Quote,
)

router = APIRouter(prefix="/api/policies", tags=["policies"])


def next_policy_number(
    session: Session,
    product_code: ProductCode,
    start: date,
) -> str:
    """Generate policy number."""
    count = session.exec(select(Policy.id)).all()

    return (
        f"PD-{product_code.value}-{start.year}-{len(count) + 1:05d}"
    )


def issue_policy(
    payload: PolicyCreate,
    session: Session,
) -> Policy:
    """Issue a policy from a quote."""

    # 1. Find quote
    quote = session.get(Quote, payload.quote_id)

    if not quote:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quote not found",
        )

    # 2. Check duplicate policy
    if quote.policy is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Quote already has a policy",
        )

    # 3. Get product
    product = quote.product

    # 4. Motor product requires vehicle registration
    if product.code == ProductCode.MOTOR:
        if not payload.vehicle_registration:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Vehicle registration is required for motor policies",
            )

    # 5. Calculate policy dates
    start_date = payload.start_date

    end_date = (
        start_date
        + timedelta(days=365 * quote.tenure_years)
        - timedelta(days=1)
    )

    # 6. Create policy
    policy = Policy(
        policy_number=next_policy_number(
            session,
            product.code,
            start_date,
        ),
        customer_id=quote.customer_id,
        product_id=quote.product_id,
        quote_id=quote.id,
        sum_insured=quote.sum_insured,
        premium=quote.premium,
        start_date=start_date,
        end_date=end_date,
        tenure_years=quote.tenure_years,
        vehicle_registration=payload.vehicle_registration,
        status=PolicyStatus.ACTIVE,
    )

    # 7. Save policy
    session.add(policy)
    session.commit()
    session.refresh(policy)

    return policy


@router.get("", response_model=list[PolicyRead])
def list_policies(
    status_filter: PolicyStatus | None = None,
    session: Session = Depends(get_session),
):
    stmt = select(Policy).order_by(Policy.created_at.desc())

    if status_filter:
        stmt = stmt.where(Policy.status == status_filter)

    return session.exec(stmt).all()


@router.post(
    "",
    response_model=PolicyRead,
    status_code=status.HTTP_201_CREATED,
)
def create_policy(
    payload: PolicyCreate,
    session: Session = Depends(get_session),
):
    return issue_policy(payload, session)


@router.get("/{policy_id}", response_model=PolicyRead)
def get_policy(
    policy_id: int,
    session: Session = Depends(get_session),
):
    policy = session.get(Policy, policy_id)

    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Policy not found",
        )

    return policy


@router.patch("/{policy_id}/status", response_model=PolicyRead)
def update_policy_status(
    policy_id: int,
    payload: PolicyStatusUpdate,
    session: Session = Depends(get_session),
):
    # 1. Find policy
    policy = session.get(Policy, policy_id)

    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Policy not found",
        )

    # 2. Cancelled is final
    if policy.status == PolicyStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cancelled policy cannot be changed",
        )

    # 3. Update status
    policy.status = payload.status

    session.add(policy)
    session.commit()
    session.refresh(policy)

    return policy
