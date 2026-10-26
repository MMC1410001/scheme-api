# The 10 template components, in the order the API template lists them. API 1 returns these first,
# then every other component SAP sends. Matched exactly against
# SAP's "Comp desc", so look-alikes such as "ALWD EB2", "ALWD VALUE SCH", "VALUE SCH %" or
# "ADDTNL PREMIUM 01" are not template components.
COMPONENTS = (
    "EB SLAB 1",
    "EB SLAB 2",
    "EB SLAB 3",
    "VALUE",
    "VALUE %",
    "VALUE FA",
    "LUXURY",
    "PREMIUM 01",
    "PREMIUM 02",
    "EB FULL MONTH",
)

# The loader stores every component SAP sends (about 100 a month; names change from month to month).
# Qualification rule (business rule, 2026-10-22): EB (EB SLAB 1-3, EB FULL MONTH), Value (VALUE, VALUE %,
# VALUE FA), ROLLER, SANDPAPER and MASKING TAPE qualify on GSR (Gross Sales Revenue); every other
# component on invoiced quantity (Q). Names match SAP's "Comp desc" exactly, so look-alikes such as
# "ALWD EB2", "EB ADD FA 02" or "ALWD VALUE SCH" qualify on invoiced quantity.
# The SAP column the GSR comes from is in GSR_COLUMN below.
GSR_COMPONENTS = (
    "EB SLAB 1",
    "EB SLAB 2",
    "EB SLAB 3",
    "EB FULL MONTH",
    "VALUE",
    "VALUE %",
    "VALUE FA",
    "ROLLER",
    "SANDPAPER",
    "MASKING TAPE",
)

# The SAP column a GSR component's Qualification is read from. SAP leaves GSR (Q) empty on
# EB FULL MONTH rows, while on the EB slab rows GSR (Q) always equals GSR (B). So EB FULL MONTH
# follows the slab working by taking its GSR from GSR (B).
GSR_COLUMN = {"EB FULL MONTH": "gsr_b"}
DEFAULT_GSR_COLUMN = "gsr_q"
INVOICE_QTY_COLUMN = "invoiced_qty_q"


def qualification_basis(component: str) -> str:
    return "GSR" if component in GSR_COMPONENTS else "INVOICE_QTY"


def qualification_column(component: str) -> str:
    if component in GSR_COMPONENTS:
        return GSR_COLUMN.get(component, DEFAULT_GSR_COLUMN)
    return INVOICE_QTY_COLUMN
