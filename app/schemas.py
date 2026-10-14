from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic.alias_generators import to_pascal


def _reject_bool(value: Any) -> Any:
    # Without this, JSON true/false would be read as 1/0.
    if isinstance(value, bool):
        raise ValueError("must be a number, not true/false")
    return value


Month = Annotated[int, Field(ge=1, le=12, description="1-12", examples=[10]), BeforeValidator(_reject_bool)]
Year = Annotated[int, Field(ge=2000, le=2100, description="Four-digit year", examples=[2026]), BeforeValidator(_reject_bool)]


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
    """API 1 body: the dealer and the month to return. Month and year may be JSON numbers or numeric strings."""

    month: Month
    year: Year


class SchemeRow(BaseModel):
    """One component row, with the columns of the API template. Keys are PascalCase, like the other dealer APIs."""

    model_config = ConfigDict(alias_generator=to_pascal, populate_by_name=True)

    customer: str
    from_date: date | None
    to_date: date | None
    component_description: str = Field(examples=["EB SLAB 1"])
    qualification: float | None = Field(
        description="GSR for EB and Value (GSR (Q); for EB FULL MONTH, GSR (B)); invoiced quantity (Q) for Luxury and Premium"
    )
    gsr_b: float | None
    rebate_rate: float | None
    clear_cut: float | None
    shortfall_amount: float | None
    shortfall_pct: float | None


class ErrorMessage(BaseModel):
    """Body of 401, 413, 500 and 503 responses."""

    detail: str


class ValidationIssue(BaseModel):
    loc: list[str | int] = Field(examples=[["body", "month"]])
    msg: str = Field(examples=["Input should be less than or equal to 12"])
    type: str = Field(examples=["less_than_equal"])


class ValidationErrorResponse(BaseModel):
    """Body of a 422: where and why the request failed. The caller's input is not echoed back."""

    detail: list[ValidationIssue]
