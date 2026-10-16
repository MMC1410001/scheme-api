# The 10 components API 1 returns, in the order the API template lists them.
# Matched exactly against SAP's "Comp desc", so look-alikes such as "ALWD EB2",
# "ALWD VALUE SCH", "VALUE SCH %" or "ADDTNL PREMIUM 01" are left out.
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

# EB and Value (VALUE, VALUE %, VALUE FA) qualify on GSR (Gross Sales Revenue); Luxury and Premium
# on invoiced quantity.
# The SAP column the GSR comes from is in QUALIFICATION_COLUMN below.
QUALIFICATION_BASIS = {
    "EB SLAB 1": "GSR",
    "EB SLAB 2": "GSR",
    "EB SLAB 3": "GSR",
    "EB FULL MONTH": "GSR",
    "VALUE": "GSR",
    "VALUE %": "GSR",
    "VALUE FA": "GSR",
    "LUXURY": "INVOICE_QTY",
    "PREMIUM 01": "INVOICE_QTY",
    "PREMIUM 02": "INVOICE_QTY",
}

# The SAP column each component's Qualification is read from. SAP leaves GSR (Q) empty on
# EB FULL MONTH rows, while on the EB slab rows GSR (Q) always equals GSR (B). So EB FULL MONTH
# follows the slab working by taking its GSR from GSR (B).
QUALIFICATION_COLUMN = {
    "EB SLAB 1": "gsr_q",
    "EB SLAB 2": "gsr_q",
    "EB SLAB 3": "gsr_q",
    "EB FULL MONTH": "gsr_b",
    "VALUE": "gsr_q",
    "VALUE %": "gsr_q",
    "VALUE FA": "gsr_q",
    "LUXURY": "invoiced_qty_q",
    "PREMIUM 01": "invoiced_qty_q",
    "PREMIUM 02": "invoiced_qty_q",
}
