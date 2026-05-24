"""Valhalla encoded polyline decoder.

Decodes Google-format encoded polylines to [lon, lat] coordinate pairs.
Valhalla uses 6-digit precision (10^6) instead of Google's standard 5-digit (10^5).
"""


def decode_polyline(
    encoded: str | None,
    precision: int = 6,
) -> list[list[float]]:
    """
    Decode an encoded polyline string to a list of [lon, lat] coordinate pairs.

    Args:
        encoded: Encoded polyline string. None or empty returns [].
        precision: Decimal precision (6 for Valhalla, 5 for Google standard).

    Returns:
        List of [lon, lat] pairs in GeoJSON coordinate order.
    """
    if not encoded:
        return []

    factor = 10**precision
    coords: list[list[float]] = []
    index = 0
    lat = 0
    lon = 0
    length = len(encoded)

    while index < length:
        # Decode latitude delta
        shift = 0
        result = 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lat += ~(result >> 1) if (result & 1) else (result >> 1)

        # Decode longitude delta
        shift = 0
        result = 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lon += ~(result >> 1) if (result & 1) else (result >> 1)

        coords.append([lon / factor, lat / factor])

    return coords
