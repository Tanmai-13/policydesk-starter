"""Quotes API.

POST /api/quotes -> look up customer and product,
run premium calculator, and save the quote.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.db import get_session
from app.models import (
    Customer,
    Product,
    Quote,
    QuoteCreate,
    QuoteRead,
)
from app.services import pricing


router = APIRouter(
    prefix="/api/quotes",
    tags=["quotes"],
)


def price_quote(
    payload: QuoteCreate,
    session: Session,
) -> tuple[float, Customer, Product]:
    """Look up customer/product and calculate premium."""

    customer = session.get(
        Customer,
        payload.customer_id,
    )

    if not customer:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Customer not found",
        )

    product = session.get(
        Product,
        payload.product_id,
    )

    if not product:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Product not found",
        )

    try:
        premium = pricing.calculate_premium(
            sum_insured=payload.sum_insured,
            base_rate=product.base_rate,
            age=pricing.age_on(
                customer.date_of_birth
            ),
            tenure_years=payload.tenure_years,
            product=product.code,
            add_ons=pricing.parse_add_ons(
                payload.add_ons
            ),
            min_sum_insured=product.min_sum_insured,
            max_sum_insured=product.max_sum_insured,
        )

    except pricing.PricingError as exc:
        detail = str(exc)

        if "below minimum" in detail.lower():
            detail = (
                "Sum insured must be at least "
                "the product minimum"
            )

        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail,
        ) from exc

    except NotImplementedError as exc:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            str(exc),
        ) from exc

    return premium, customer, product


@router.get(
    "",
    response_model=list[QuoteRead],
)
def list_quotes(
    session: Session = Depends(get_session),
):
    return session.exec(
        select(Quote).order_by(
            Quote.created_at.desc()
        )
    ).all()


@router.post(
    "",
    response_model=QuoteRead,
    status_code=status.HTTP_201_CREATED,
)
def create_quote(
    payload: QuoteCreate,
    session: Session = Depends(get_session),
):
    premium, _, _ = price_quote(
        payload,
        session,
    )

    quote = Quote(
        **payload.model_dump(),
        premium=premium,
    )

    session.add(quote)
    session.commit()
    session.refresh(quote)

    return quote


@router.get(
    "/{quote_id}",
    response_model=QuoteRead,
)
def get_quote(
    quote_id: int,
    session: Session = Depends(get_session),
):
    quote = session.get(
        Quote,
        quote_id,
    )

    if not quote:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Quote not found",
        )

    return quote
