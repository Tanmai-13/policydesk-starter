"""Claims API."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.db import get_session
from app.models import (
    Claim,
    ClaimCreate,
    ClaimRead,
    ClaimStatus,
    ClaimStatusUpdate,
    Policy,
    PolicyStatus,
    ProductCode,
)

router = APIRouter(prefix="/api/claims", tags=["claims"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def approved_total(session: Session, policy_id: int) -> float:
    amounts = session.exec(
        select(Claim.amount).where(
            Claim.policy_id == policy_id,
            Claim.status == ClaimStatus.APPROVED,
        )
    ).all()

    return float(sum(amounts)) if amounts else 0.0


def remaining_cover(session: Session, policy: Policy) -> float:
    return policy.sum_insured - approved_total(session, policy.id)


# --------------------------------------------------------------------------- #
# Filing
# --------------------------------------------------------------------------- #

def file_claim(payload: ClaimCreate, session: Session) -> Claim:
    policy = session.get(Policy, payload.policy_id)

    # 1. Policy must exist
    if not policy:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Policy not found",
        )

    # 2. Cancelled policy -> save as Rejected
    if policy.status == PolicyStatus.CANCELLED:
        claim = Claim.model_validate(payload)
        claim.status = ClaimStatus.REJECTED
        claim.reason = "Policy is cancelled"

        session.add(claim)
        session.commit()
        session.refresh(claim)

        return claim

    # 3. Only Active policies accept claims
    if policy.status != PolicyStatus.ACTIVE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Policy is {policy.status.value.lower()}; "
            "only Active policies accept claims",
        )

    # 4. Incident date validation
    if not (
        policy.start_date
        <= payload.incident_date
        <= policy.end_date
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Incident date must fall within the policy period "
            f"{policy.start_date} to {policy.end_date}",
        )

    # 5. Remaining cover validation
    remaining = remaining_cover(session, policy)

    if payload.amount > remaining:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Claim amount exceeds remaining cover of "
            f"{remaining:,.2f}",
        )

    # 6. Motor registration validation
    if policy.product.code == ProductCode.MOTOR:
        registration = (
            getattr(payload, "vehicle_registration", None) or ""
        ).strip()

        if not registration:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Motor claims need a vehicle registration number",
            )

    claim = Claim.model_validate(payload)

    session.add(claim)
    session.commit()
    session.refresh(claim)

    return claim


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("", response_model=list[ClaimRead])
def list_claims(
    policy_id: int | None = None,
    status: ClaimStatus | None = None,
    session: Session = Depends(get_session),
):
    stmt = select(Claim).order_by(Claim.created_at.desc())

    if policy_id is not None:
        stmt = stmt.where(Claim.policy_id == policy_id)

    if status is not None:
        stmt = stmt.where(Claim.status == status)

    return session.exec(stmt).all()


@router.post(
    "",
    response_model=ClaimRead,
    status_code=status.HTTP_201_CREATED,
)
def create_claim(
    payload: ClaimCreate,
    session: Session = Depends(get_session),
):
    return file_claim(payload, session)


@router.get("/{claim_id}", response_model=ClaimRead)
def get_claim(
    claim_id: int,
    session: Session = Depends(get_session),
):
    claim = session.get(Claim, claim_id)

    if not claim:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Claim not found",
        )

    return claim


# --------------------------------------------------------------------------- #
# Status workflow
# --------------------------------------------------------------------------- #

@router.patch("/{claim_id}/status", response_model=ClaimRead)
def update_claim_status(
    claim_id: int,
    payload: ClaimStatusUpdate,
    session: Session = Depends(get_session),
):
    claim = session.get(Claim, claim_id)

    if not claim:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Claim not found",
        )

    current_status = claim.status
    new_status = payload.status

    # Allowed status transitions
    allowed_transitions = {
        ClaimStatus.FILED: {
            ClaimStatus.UNDER_REVIEW,
            ClaimStatus.REJECTED,
        },
        ClaimStatus.UNDER_REVIEW: {
            ClaimStatus.APPROVED,
            ClaimStatus.REJECTED,
        },
        ClaimStatus.APPROVED: set(),
        ClaimStatus.REJECTED: set(),
    }

    # Approved and Rejected are final statuses
    if current_status in {
        ClaimStatus.APPROVED,
        ClaimStatus.REJECTED,
    }:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Claim in {current_status.value} status "
            "cannot be changed",
        )

    # Validate status transition
    if new_status not in allowed_transitions.get(
        current_status,
        set(),
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Invalid transition from "
            f"{current_status.value} to {new_status.value}",
        )

    # Check remaining cover before approval
    if new_status == ClaimStatus.APPROVED:
        policy = session.get(Policy, claim.policy_id)

        if not policy:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Policy not found",
            )

        remaining = remaining_cover(session, policy)

        if claim.amount > remaining:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Claim amount exceeds remaining cover of "
                f"{remaining:,.2f}",
            )

    # Update claim status
    claim.status = new_status

    # Save decision reason, if provided
    if payload.reason is not None:
        claim.reason = payload.reason

    session.add(claim)
    session.commit()
    session.refresh(claim)

    return claim