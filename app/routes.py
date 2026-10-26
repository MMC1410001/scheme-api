from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Response, Security
from pydantic import TypeAdapter

from app.cache import ResponseCache, get_cache
from app.normalize import normalize_dealer_code
from app.openapi_examples import API1_REQUESTS, API1_ROWS, ERRORS, ok
from app.repository import Repository, get_repository
from app.constants import COMPONENTS
from app.schemas import OTHER_COMPONENT_FIELDS, SchemeMonthRequest, SchemeRow
from app.security import SCOPE, require_token

# API 1 rows go through the response model (PascalCase keys, numbers as floats), like FastAPI does.
SCHEME_ROWS = TypeAdapter(list[SchemeRow])


def _json_response(cache: ResponseCache, key: str, build: Callable[[], bytes]) -> Response:
    body, status = cache.get_or_build(key, build)
    headers = {"X-Cache": status} if status else None
    return Response(content=body, media_type="application/json", headers=headers)


router = APIRouter(
    prefix="/v1/schemes", tags=["schemes"], dependencies=[Security(require_token, scopes=[SCOPE])], responses=ERRORS
)


@router.post(
    "/current-month",
    response_model=list[SchemeRow],
    summary="API 1: a dealer's scheme rows for the requested month",
    responses=ok(API1_ROWS),
)
def month_schemes(
    body: Annotated[SchemeMonthRequest, Body(openapi_examples=API1_REQUESTS)],
    repo: Repository = Depends(get_repository),
    cache: ResponseCache = Depends(get_cache),
) -> Response:
    """The dealer's scheme rows for the given month and year: every component SAP sends (EB SLAB 1-3,
    VALUE, VALUE %, VALUE FA, LUXURY, PREMIUM 01/02, EB FULL MONTH and the others), ordered by
    ClearCut, highest first (empty ClearCut last). Rows with the same ClearCut keep the API template's
    order: by end date, then the 10 in template order, then other components by name.

    A row belongs to every month its validity period overlaps (rows without dates: the GCS month
    folder its file came from). Each component comes back once per start date: SAP's newest update,
    even if SAP changed the end date or re-issued the scheme under a new agreement number.
    Only the current month and the two before it are kept; older months return an empty list.
    Returns an empty list when the dealer has no data for that month.

    Qualification is GSR for EB (EB SLAB 1-3; EB FULL MONTH takes GSR (B)), Value (VALUE, VALUE %,
    VALUE FA), ROLLER, SANDPAPER and MASKING TAPE, and invoiced quantity (Q) for every other component.
    Rows of components outside the 10 also carry QualificationBasis ("GSR" or "INVOICE_QTY", what
    Qualification holds) and InvoicedQty (Invoiced Quantity (Q)); rows of the 10 have neither.
    """
    dealer_code = normalize_dealer_code(body.dealer_code)

    def build() -> bytes:
        # Rows of the 10 components go without the extra fields (left out by exclude_unset), as agreed.
        rows = [{k: v for k, v in row.items() if k not in OTHER_COMPONENT_FIELDS}
                if row["component_description"] in COMPONENTS else row
                for row in repo.month_schemes(dealer_code, body.year, body.month)]
        return SCHEME_ROWS.dump_json(SCHEME_ROWS.validate_python(rows), by_alias=True, exclude_unset=True)

    # "cc" marks the ClearCut order (2026-10-22), so answers cached in the older order are not served.
    return _json_response(cache, f"api1:cc:{dealer_code}:{body.year}:{body.month}", build)

