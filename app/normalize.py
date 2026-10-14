def normalize_dealer_code(value: object) -> str | None:
    """Make dealer codes comparable.

    SAP pads codes with leading zeros, Excel often strips them (or turns them
    into floats like 1234567.0), and callers may send either form.
    """
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        text = text.lstrip("0") or "0"
    return text
