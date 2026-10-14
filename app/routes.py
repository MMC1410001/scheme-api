from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Response
from pydantic import TypeAdapter

from app.cache import ResponseCache, get_cache
from app.normalize import normalize_dealer_code
from app.openapi_examples import API1_REQUESTS, API1_ROWS, ERRORS, ok  # API2_REQUESTS, API2_ROWS: API 2 on hold
from app.repository import Repository, get_repository
from app.schemas import SchemeMonthRequest, SchemeRow  # DealerRequest: API 2 on hold
from app.security import require_api_key

# API 1 rows go through the response model (PascalCase keys, numbers as floats), like FastAPI does.
SCHEME_ROWS = TypeAdapter(list[SchemeRow])


def _json_response(cache: ResponseCache, key: str, build: Callable[[], bytes]) -> Response:
    body, status = cache.get_or_build(key, build)
    headers = {"X-Cache": status} if status else None
    return Response(content=body, media_type="application/json", headers=headers)


# --- API 2 is on hold (2026-10-15). Uncomment to restore. ---
# def _dump(rows: list[dict[str, Any]]) -> bytes:
#     # Same output as FastAPI's JSONResponse.
#     return json.dumps(rows, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


router = APIRouter(
    prefix="/v1/schemes", tags=["schemes"], dependencies=[Depends(require_api_key)], responses=ERRORS
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

    A row's month is the GCS month folder its SAP file came from. Returns an empty list when the
    dealer has no data for that month.
    """
    dealer_code = normalize_dealer_code(body.dealer_code)

    def build() -> bytes:
        rows = repo.month_schemes(dealer_code, body.year, body.month)
        return SCHEME_ROWS.dump_json(SCHEME_ROWS.validate_python(rows), by_alias=True)

    return _json_response(cache, f"api1:{dealer_code}:{body.year}:{body.month}", build)


# --- API 2 is on hold (2026-10-15): POST /v1/schemes/report is not served. Uncomment to restore. ---
# @router.post(
#     "/report",
#     response_model=list[dict[str, Any]],
#     summary="API 2: a dealer's row from the latest generated report",
#     responses=ok(API2_ROWS),
# )
# def dealer_report(
#     body: Annotated[DealerRequest, Body(openapi_examples=API2_REQUESTS)],
#     repo: Repository = Depends(get_repository),
#     cache: ResponseCache = Depends(get_cache),
# ) -> Response:
#     """The dealer's row from the latest report the scheme platform put in GCS
#     (newest month folder, then highest version).
#
#     Keys are the report's two header rows joined and snake_cased, in file order, e.g.
#     "Value Slab" + "GSR(Q) Value" becomes "value_slab_gsr_q_value". Empty cells are null.
#     Returns an empty list when no report has been loaded or the dealer is not in it.
#     """
#     dealer_code = normalize_dealer_code(body.dealer_code)
#
#     def build() -> bytes:
#         latest = repo.latest_report()
#         return _dump(repo.report_rows(latest, dealer_code) if latest else [])
#
#     return _json_response(cache, f"api2:{dealer_code}", build)
