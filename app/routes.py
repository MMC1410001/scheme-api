from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Response, Security
from pydantic import TypeAdapter

from app.cache import ResponseCache, get_cache
from app.normalize import normalize_dealer_code
from app.openapi_examples import API1_REQUESTS, API1_ROWS, ERRORS, ok
from app.repository import Repository, get_repository
from app.schemas import SchemeMonthRequest, SchemeRow
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
    """The dealer's EB SLAB 1-3, VALUE, LUXURY, PREMIUM 01/02 and EB FULL MONTH rows for the given
    month and year, ordered by end date (the API template's order).

    A row belongs to every month its validity period overlaps (rows without dates: the GCS month
    folder its file came from). Returns an empty list when the dealer has no data for that month.
    """
    dealer_code = normalize_dealer_code(body.dealer_code)

    def build() -> bytes:
        rows = repo.month_schemes(dealer_code, body.year, body.month)
        return SCHEME_ROWS.dump_json(SCHEME_ROWS.validate_python(rows), by_alias=True)

    return _json_response(cache, f"api1:{dealer_code}:{body.year}:{body.month}", build)

