"""
Route infrastructure analyser using Overpass API.

Implements [cycle-route-assessment:FR-002] - Query Overpass for way tags, determine provision
Implements [cycle-route-assessment:NFR-003] - Rate limiting for Overpass calls

Implements test scenarios:
- [cycle-route-assessment:InfrastructureAnalyser/TS-01] Segregated cycleway detected
- [cycle-route-assessment:InfrastructureAnalyser/TS-02] Shared-use path detected
- [cycle-route-assessment:InfrastructureAnalyser/TS-03] On-road lane detected
- [cycle-route-assessment:InfrastructureAnalyser/TS-04] No provision detected
- [cycle-route-assessment:InfrastructureAnalyser/TS-05] Speed limit inferred
- [cycle-route-assessment:InfrastructureAnalyser/TS-06] Unknown surface handled
"""

import math
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# UK default speed limits by highway classification (mph)
UK_DEFAULT_SPEEDS: dict[str, int] = {
    "motorway": 70,
    "trunk": 60,
    "primary": 60,
    "secondary": 60,
    "tertiary": 30,
    "unclassified": 30,
    "residential": 30,
    "living_street": 20,
    "service": 20,
    "cycleway": 0,
    "path": 0,
    "footway": 0,
    "bridleway": 0,
    "track": 0,
    "pedestrian": 0,
}

# Overpass API endpoint
OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"


@dataclass
class RouteSegment:
    """A segment of route with consistent infrastructure provision."""

    way_id: int
    provision: str  # segregated, shared_use, on_road_lane, advisory_lane, none
    highway: str  # OSM highway classification
    speed_limit: int  # mph (0 for off-road)
    surface: str  # asphalt, gravel, unpaved, unknown, etc.
    lit: bool | None  # True/False/None if unknown
    distance_m: float  # approximate segment length in metres
    name: str  # road/path name if available
    original_provision: str | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "way_id": self.way_id,
            "provision": self.provision,
            "highway": self.highway,
            "speed_limit": self.speed_limit,
            "surface": self.surface,
            "lit": self.lit,
            "distance_m": round(self.distance_m, 1),
            "name": self.name,
        }
        if self.original_provision is not None:
            result["original_provision"] = self.original_provision
        return result


def classify_provision(tags: dict[str, str]) -> str:
    """
    Determine cycling provision type from OSM way tags.

    Returns one of: segregated, shared_use, on_road_lane, advisory_lane, none
    """
    highway = tags.get("highway", "")

    # Dedicated cycleways
    if highway == "cycleway":
        return "segregated"

    # Shared-use paths
    if highway in ("path", "footway", "bridleway"):
        bicycle = tags.get("bicycle", "")
        foot = tags.get("foot", "")
        designation = tags.get("designation", "")
        if bicycle in ("designated", "yes") and foot in ("designated", "yes"):
            return "shared_use"
        if bicycle in ("designated", "yes"):
            return "segregated"
        if "shared" in designation.lower():
            return "shared_use"
        # Paths with no specific cycling designation
        if bicycle == "yes":
            return "shared_use"
        return "none"

    # Roads - check for cycle lanes
    cycleway = tags.get("cycleway", "")
    cycleway_left = tags.get("cycleway:left", "")
    cycleway_right = tags.get("cycleway:right", "")
    cycleway_both = tags.get("cycleway:both", "")

    all_cycleway_tags = [cycleway, cycleway_left, cycleway_right, cycleway_both]

    if any(t in ("track", "separate") for t in all_cycleway_tags):
        return "segregated"
    if any(t == "lane" for t in all_cycleway_tags):
        return "on_road_lane"
    if any(t in ("shared_lane", "share_busway") for t in all_cycleway_tags):
        return "advisory_lane"

    return "none"


def extract_speed_limit(tags: dict[str, str]) -> int:
    """
    Extract speed limit from tags, falling back to UK defaults by highway type.

    Returns speed in mph. Off-road paths return 0.
    """
    maxspeed = tags.get("maxspeed", "")
    if maxspeed:
        # Parse "30 mph", "30", "20 mph", etc.
        try:
            return int(maxspeed.split()[0])
        except (ValueError, IndexError):
            pass

    highway = tags.get("highway", "")
    return UK_DEFAULT_SPEEDS.get(highway, 30)


def extract_surface(tags: dict[str, str]) -> str:
    """Extract surface type from tags, defaulting to 'unknown'."""
    return tags.get("surface", "unknown")


def extract_lit(tags: dict[str, str]) -> bool | None:
    """Extract lighting status from tags."""
    lit = tags.get("lit", "")
    if lit == "yes":
        return True
    if lit == "no":
        return False
    return None


def build_overpass_query(coordinates: list[list[float]], buffer_m: int = 20) -> str:
    """
    Build an Overpass query to fetch way tags along a route.

    Args:
        coordinates: List of [lon, lat] pairs from the route geometry.
        buffer_m: Buffer around route points in metres.

    Returns:
        Overpass QL query string.
    """
    # Sample coordinates to avoid huge queries (every 5th point, min 3)
    step = max(1, len(coordinates) // 50)
    sampled = coordinates[::step]
    if coordinates[-1] not in sampled:
        sampled.append(coordinates[-1])

    # Build around statements for sampled points
    around_parts = []
    for lon, lat in sampled:
        around_parts.append(f"{lat},{lon}")

    coords_str = ",".join(around_parts)

    query = f"""
[out:json][timeout:25];
(
  way(around:{buffer_m},{coords_str})["highway"];
);
out geom;
"""
    return query.strip()


def parse_overpass_ways(
    overpass_response: dict[str, Any],
    route_distance_m: float,
) -> list[RouteSegment]:
    """
    Parse Overpass response into route segments.

    Each OSM way becomes a segment. Distance is estimated by dividing the total
    route distance proportionally among ways (exact per-way distance would
    require geometry intersection which is out of scope).

    Args:
        overpass_response: JSON response from Overpass API.
        route_distance_m: Total route distance in metres (from OSRM).

    Returns:
        List of RouteSegment objects.
    """
    elements = overpass_response.get("elements", [])
    ways = [e for e in elements if e.get("type") == "way"]

    if not ways:
        return []

    # Estimate per-way distance (equal division as approximation)
    per_way_distance = route_distance_m / len(ways) if ways else 0

    segments = []
    for way in ways:
        tags = way.get("tags", {})
        highway = tags.get("highway", "unknown")

        # Skip non-routable highways
        if highway in ("proposed", "construction", "abandoned", "razed", "platform"):
            continue

        segment = RouteSegment(
            way_id=way.get("id", 0),
            provision=classify_provision(tags),
            highway=highway,
            speed_limit=extract_speed_limit(tags),
            surface=extract_surface(tags),
            lit=extract_lit(tags),
            distance_m=per_way_distance,
            name=tags.get("name", "Unnamed"),
        )
        segments.append(segment)

    return segments


def summarise_provision(segments: list[RouteSegment]) -> dict[str, float]:
    """
    Summarise the provision breakdown as distance (metres) per type.

    Returns dict mapping provision type to total distance in metres.
    """
    breakdown: dict[str, float] = {}
    for seg in segments:
        breakdown[seg.provision] = breakdown.get(seg.provision, 0) + seg.distance_m
    return {k: round(v, 1) for k, v in breakdown.items()}


# Provision quality ranking for parallel detection (higher = better)
PROVISION_RANK: dict[str, int] = {
    "segregated": 4,
    "shared_use": 3,
    "on_road_lane": 2,
    "advisory_lane": 1,
    "none": 0,
}

# Provisions that are candidates for upgrade via parallel detection
POOR_PROVISIONS = {"none", "advisory_lane", "on_road_lane"}

# Highway types that are roads (not off-road paths)
ROAD_HIGHWAY_TYPES = {
    "residential", "tertiary", "secondary", "primary",
    "trunk", "unclassified", "living_street", "service",
}


def detect_parallel_provision(
    segments: list[RouteSegment],
    overpass_data: dict[str, Any],
) -> list[RouteSegment]:
    """
    Scan Overpass data for cycleways parallel to road segments with poor provision.

    For each road segment with poor provision, checks if any cycleway/designated-path
    way in the Overpass data has similar bearing and hasn't already been included as
    a route segment. If found, upgrades the segment's provision.

    Args:
        segments: Route segments parsed from Overpass data.
        overpass_data: Raw Overpass JSON response (with geometry from out geom).

    Returns:
        The same segments list with provisions upgraded where parallel
        infrastructure was detected.
    """
    # Collect way_ids already in route segments
    segment_way_ids = {seg.way_id for seg in segments}

    # Find candidate cycleways from the Overpass data
    elements = overpass_data.get("elements", [])
    candidates = []
    for elem in elements:
        if elem.get("type") != "way":
            continue
        if elem.get("id") in segment_way_ids:
            continue
        tags = elem.get("tags", {})
        highway = tags.get("highway", "")
        bicycle = tags.get("bicycle", "")

        is_cycleway = highway == "cycleway"
        is_designated_path = highway in ("path", "footway") and bicycle in ("designated", "yes")

        if not is_cycleway and not is_designated_path:
            continue

        geometry = elem.get("geometry", [])
        candidate_bearing = calculate_way_bearing(geometry)
        if candidate_bearing is None:
            continue

        provision = classify_provision(tags)
        candidates.append({
            "way_id": elem["id"],
            "bearing": candidate_bearing,
            "provision": provision,
            "geometry": geometry,
        })

    if not candidates:
        return segments

    # For each poor-provision road segment, find matching parallel cycleways
    for seg in segments:
        if seg.provision not in POOR_PROVISIONS:
            continue
        if seg.highway not in ROAD_HIGHWAY_TYPES:
            continue

        # Find this segment's way in the overpass data to get its geometry
        seg_bearing = None
        for elem in elements:
            if elem.get("type") == "way" and elem.get("id") == seg.way_id:
                geometry = elem.get("geometry", [])
                seg_bearing = calculate_way_bearing(geometry)
                break

        if seg_bearing is None:
            continue

        # Find best matching candidate
        best_candidate = None
        best_rank = PROVISION_RANK.get(seg.provision, 0)

        for cand in candidates:
            diff = bearing_difference(seg_bearing, cand["bearing"])
            if diff > 30:
                continue

            cand_rank = PROVISION_RANK.get(cand["provision"], 0)
            if cand_rank > best_rank:
                best_candidate = cand
                best_rank = cand_rank

        if best_candidate:
            seg.original_provision = seg.provision
            seg.provision = best_candidate["provision"]

    return segments


def calculate_way_bearing(geometry: list[dict]) -> float | None:
    """
    Calculate compass bearing of an OSM way from first to last node.

    Args:
        geometry: List of {lat, lon} dicts from Overpass out geom response.

    Returns:
        Bearing in degrees (0-360), or None if fewer than 2 nodes.
    """
    if len(geometry) < 2:
        return None

    lat1 = math.radians(geometry[0]["lat"])
    lon1 = math.radians(geometry[0]["lon"])
    lat2 = math.radians(geometry[-1]["lat"])
    lon2 = math.radians(geometry[-1]["lon"])

    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(x, y))
    return bearing % 360


def bearing_difference(bearing_a: float, bearing_b: float) -> float:
    """
    Calculate minimum angular difference between two bearings.

    Parallel ways going in opposite directions (180 degrees apart)
    are treated as having 0 degree difference.

    Returns:
        Difference in degrees (0-90).
    """
    diff = abs(bearing_a - bearing_b) % 360
    if diff > 180:
        diff = 360 - diff
    # Opposite directions are parallel
    if diff > 90:
        diff = 180 - diff
    return diff
