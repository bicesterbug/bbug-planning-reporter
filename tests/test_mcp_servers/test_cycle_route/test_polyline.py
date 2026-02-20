"""Tests for Valhalla encoded polyline decoder."""

import pytest

from src.mcp_servers.cycle_route.polyline import decode_polyline


def _encode_polyline(coords: list[list[float]], precision: int = 6) -> str:
    """Test helper: encode [lon, lat] pairs to polyline string."""
    encoded = []
    prev_lat = 0
    prev_lon = 0
    factor = 10**precision

    for lon, lat in coords:
        lat_int = round(lat * factor)
        lon_int = round(lon * factor)
        d_lat = lat_int - prev_lat
        d_lon = lon_int - prev_lon
        prev_lat = lat_int
        prev_lon = lon_int

        for val in (d_lat, d_lon):
            val = ~(val << 1) if val < 0 else val << 1
            while val >= 0x20:
                encoded.append(chr((0x20 | (val & 0x1F)) + 63))
                val >>= 5
            encoded.append(chr(val + 63))

    return "".join(encoded)


class TestDecodePolylineKnownCoords:
    """Decode known encoded strings to expected coordinates."""

    def test_single_point(self):
        coords = [[-1.153400, 51.899700]]
        encoded = _encode_polyline(coords)
        result = decode_polyline(encoded)

        assert len(result) == 1
        assert abs(result[0][0] - (-1.153400)) < 1e-6
        assert abs(result[0][1] - 51.899700) < 1e-6

    def test_multiple_points(self):
        coords = [
            [-1.153400, 51.899700],
            [-1.151000, 51.901000],
            [-1.148000, 51.902500],
            [-1.145000, 51.905000],
        ]
        encoded = _encode_polyline(coords)
        result = decode_polyline(encoded)

        assert len(result) == 4
        for i, (expected, actual) in enumerate(zip(coords, result)):
            assert abs(actual[0] - expected[0]) < 1e-6, f"Point {i} lon mismatch"
            assert abs(actual[1] - expected[1]) < 1e-6, f"Point {i} lat mismatch"

    def test_coordinates_in_lon_lat_order(self):
        """Decoded coordinates are in [lon, lat] order (GeoJSON convention)."""
        coords = [[-1.15, 51.90]]
        encoded = _encode_polyline(coords)
        result = decode_polyline(encoded)

        lon, lat = result[0]
        assert abs(lon - (-1.15)) < 1e-6
        assert abs(lat - 51.90) < 1e-6


class TestDecodePolylineEmpty:
    """Handle empty/None input."""

    def test_empty_string(self):
        assert decode_polyline("") == []

    def test_none_input(self):
        assert decode_polyline(None) == []


class TestDecodePolylineRoundTrip:
    """Round-trip encode/decode preserves coordinates."""

    @pytest.mark.parametrize("coords", [
        [[-1.15, 51.90], [-1.14, 51.91]],
        [[-0.001, 0.001]],
        [[-1.2, 51.8], [-1.1, 51.9], [-1.0, 52.0]],
    ])
    def test_round_trip(self, coords):
        encoded = _encode_polyline(coords)
        result = decode_polyline(encoded)

        assert len(result) == len(coords)
        for expected, actual in zip(coords, result):
            assert abs(actual[0] - expected[0]) < 1e-6
            assert abs(actual[1] - expected[1]) < 1e-6

    def test_precision_5_google_standard(self):
        """Supports Google standard 5-digit precision when specified."""
        coords = [[-1.15340, 51.89970]]
        encoded = _encode_polyline(coords, precision=5)
        result = decode_polyline(encoded, precision=5)

        assert len(result) == 1
        assert abs(result[0][0] - (-1.15340)) < 1e-5
        assert abs(result[0][1] - 51.89970) < 1e-5
