"""Claims API.

POST /api/claims             -> file a claim on a policy (201)
GET  /api/claims             -> list claims, newest first (?policy_id= and ?status= are optional filters)
GET  /api/claims/{claim_id}  -> one claim (404 if missing)

Rules for filing, checked in this order
    1. 404 if the policy does not exist
    2. Cancelled policy   -> the claim is SAVED as Rejected with reason "Policy is cancelled" (201, not an error)
    3. not Active         -> 422 "Policy is <status>; only Active policies accept claims"
    4. incident date must fall inside the policy period (start and end inclusive) -> 422
    5. amount must not exceed remaining cover (sum insured - claims already Approved) -> 422
    6. Motor claims need a vehicle registration number -> 422

The PATCH /status workflow is Phase 3.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.db import get_session
from app.models import Claim, ClaimCreate, ClaimRead, ClaimStatus, Policy, PolicyStatus, ProductCode

router = APIRouter(prefix="/api/claims", tags=["claims"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def approved_total(session: Session, policy_id: int) -> float:
    """Sum of Claim.amount for APPROVED claims on a policy; 0.0 if none."""
    amounts = session.exec(
        select(Claim.amount).where(Claim.policy_id == policy_id, Claim.status == ClaimStatus.APPROVED)
    ).all()
    return float(sum(amounts)) if amounts else 0.0


def remaining_cover(session: Session, policy: Policy) -> float:
    """Sum insured minus what has already been approved."""
    return policy.sum_insured - approved_total(session, policy.id)


# --------------------------------------------------------------------------- #
# Filing
# --------------------------------------------------------------------------- #
def file_claim(payload: ClaimCreate, session: Session) -> Claim:
    """Shared by the API and the HTML form. Raise HTTPException with the right status code on failure."""
    # 1. policy must exist
    policy = session.get(Policy, payload.policy_id)
    if not policy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")

    # 2. cancelled -> saved as Rejected, not an error
    if policy.status == PolicyStatus.CANCELLED:
        claim = Claim.model_validate(payload)
        claim.status = ClaimStatus.REJECTED
        claim.reason = "Policy is cancelled"
        session.add(claim)
        session.commit()
        session.refresh(claim)
        return claim

    # 3. only Active policies accept claims
    if policy.status != PolicyStatus.ACTIVE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Policy is {policy.status.value.lower()}; only Active policies accept claims",
        )

    # 4. incident inside the policy period
    if not (policy.start_date <= payload.incident_date <= policy.end_date):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Incident date must fall within the policy period {policy.start_date} to {policy.end_date}",
        )

    # 5. amount within remaining cover
    remaining = remaining_cover(session, policy)
    if payload.amount > remaining:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Claim amount exceeds remaining cover of {remaining:,.2f}",
        )

    # 6. Motor claims need a vehicle registration
    if policy.product.code == ProductCode.MOTOR and not (payload.vehicle_registration or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Motor claims need a vehicle registration number")

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


@router.post("", response_model=ClaimRead, status_code=status.HTTP_201_CREATED)
def create_claim(payload: ClaimCreate, session: Session = Depends(get_session)):
    return file_claim(payload, session)


@router.get("/{claim_id}", response_model=ClaimRead)
def get_claim(claim_id: int, session: Session = Depends(get_session)):
    claim = session.get(Claim, claim_id)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim not found")
    return claim