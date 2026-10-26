import re
from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic.alias_generators import to_pascal


def _reject_bool(value: Any) -> Any:
    # Without this, JSON true/false would be read as 1/0.
    if isinstance(value, bool):
        raise ValueError("must be a number, not true/false")
    return value


def _digits_only(pattern: str, message: str):
    """Accept a JSON integer, or a string matching pattern; no sign, decimal, exponent or spaces."""

    def check(value: Any) -> Any:
        if isinstance(value, str) and not re.fullmatch(pattern, value):
            raise ValueError(message)
        if isinstance(value, float):
            raise ValueError(f"{message}, not a decimal")
        return _reject_bool(value)

    return BeforeValidator(check)


Month = Annotated[int, Field(ge=1, le=12, description="1-12 (9 or 09)", examples=[10]),
                  _digits_only(r"0?[1-9]|1[0-2]", "must be 1-12, written as one or two digits")]
Year = Annotated[int, Field(ge=2000, le=2100, description="Four-digit year, 2000-2100", examples=[2026]),
                 _digits_only(r"[0-9]{4}", "must be a four-digit year")]


class DealerRequest(BaseModel):
    # Accept the code as a JSON string or number (6100000001 or "6100000001").
    model_config = ConfigDict(str_strip_whitespace=True, coerce_numbers_to_str=True)

    dealer_code: str = Field(
        min_length=1,
        max_length=20,
        pattern=r"^[A-Za-z0-9]+$",
        description="SAP customer code; letters and digits only. Leading zeros are ignored.",
        examples=["6100000001"],
    )


class SchemeMonthRequest(DealerRequest):
    """API 1 body: the dealer and the month to return. Month is 1-12, sent as 9 or 09 (number or string); year is four digits, 2000-2100 (number or string)."""

    month: Month
    year: Year


class SchemeRow(BaseModel):
    """One component row, with the columns of the API template. Keys are PascalCase, like the other dealer APIs."""

    model_config = ConfigDict(alias_generator=to_pascal, populate_by_name=True)

    customer: str
    from_date: date | None
    to_date: date | None
    component_description: str = Field(
        examples=["EB SLAB 1"],
        description="SAP Comp desc: the 10 template components first, then any other component SAP sends",
    )
    qualification: float | None = Field(
        description="GSR (Q) for EB, Value (VALUE, VALUE %, VALUE FA), ROLLER, SANDPAPER and MASKING TAPE (for "
        "EB FULL MONTH, GSR (B)); invoiced quantity (Q) for every other component"
    )
    gsr_b: float | None
    rebate_rate: float | None
    clear_cut: float | None
    shortfall_amount: float | None
    shortfall_pct: float | None
    next_slab_rate: float | None = Field(description="Rebate rate of the next slab (SAP Next Slab Rate)")
    # Only on rows of components outside the 10 template ones (left out of the others).
    qualification_basis: str | None = Field(
        default=None,
        description="Only for components outside the 10 template ones: what Qualification holds, GSR (GSR (Q); "
        "ROLLER, SANDPAPER, MASKING TAPE) or INVOICE_QTY (invoiced quantity (Q); the others). Not sent for the 10.",
        examples=["INVOICE_QTY"],
    )
    invoiced_qty: float | None = Field(
        default=None,
        description="Only for components outside the 10 template ones: SAP Invoiced Quantity (Q) (equal to "
        "Qualification when QualificationBasis is INVOICE_QTY). Not sent for the 10.",
        examples=[50.0],
    )


# SchemeRow fields sent only on rows of components outside the 10 template ones.
OTHER_COMPONENT_FIELDS = {"qualification_basis", "invoiced_qty"}


class TokenResponse(BaseModel):
    """RFC 6749 section 5.1 access token response."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str


class OAuthErrorResponse(BaseModel):
    """RFC 6749 section 5.2 error from the token endpoint."""

    error: str
    error_description: str


class ErrorMessage(BaseModel):
    """Body of 401, 403, 413, 429, 500 and 503 responses."""

    detail: str


class ValidationIssue(BaseModel):
    loc: list[str | int] = Field(examples=[["body", "month"]])
    msg: str = Field(examples=["Input should be less than or equal to 12"])
    type: str = Field(examples=["less_than_equal"])


class ValidationErrorResponse(BaseModel):
    """Body of a 422: where and why the request failed. The caller's input is not echoed back."""

    detail: list[ValidationIssue]
