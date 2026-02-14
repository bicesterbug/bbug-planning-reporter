"""
Destinations API router.

Implements [cycle-route-assessment:FR-005] - Configurable destinations with defaults
Implements [cycle-route-assessment:FR-006] - Per-review destination selection
Implements [cycle-route-assessment:DestinationManagement/TS-04] - List destinations via API
"""

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.dependencies import RedisClientDep
from src.shared.destinations import (
    add_destination,
    delete_destination,
    list_destinations,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


class DestinationCreate(BaseModel):
    """Request body for creating a destination."""

    name: str = Field(..., description="Destination name", min_length=1, max_length=200)
    lat: float = Field(..., description="Latitude", ge=-90, le=90)
    lon: float = Field(..., description="Longitude", ge=-180, le=180)
    category: str = Field(
        default="other",
        description="Category: rail, bus, or other",
        pattern="^(rail|bus|other)$",
    )


class DestinationResponse(BaseModel):
    """Single destination in response."""

    id: str
    name: str
    lat: float
    lon: float
    category: str


class DestinationListResponse(BaseModel):
    """Response for GET /destinations."""

    destinations: list[DestinationResponse]
    total: int


class DestinationDeleteResponse(BaseModel):
    """Response for DELETE /destinations/{id}."""

    deleted: bool
    destination_id: str


@router.get(
    "/destinations",
    response_model=DestinationListResponse,
)
async def get_destinations(
    redis: RedisClientDep,
) -> DestinationListResponse:
    """
    List all configured cycle route destinations.

    Implements [cycle-route-assessment:DestinationManagement/TS-04]
    """
    destinations = await list_destinations(redis)
    return DestinationListResponse(
        destinations=[DestinationResponse(**d) for d in destinations],
        total=len(destinations),
    )


@router.post(
    "/destinations",
    response_model=DestinationResponse,
    status_code=201,
)
async def create_destination(
    body: DestinationCreate,
    redis: RedisClientDep,
) -> dict[str, Any]:
    """
    Add a new cycle route destination.

    Implements [cycle-route-assessment:DestinationManagement/TS-02]
    """
    dest = await add_destination(
        redis,
        name=body.name,
        lat=body.lat,
        lon=body.lon,
        category=body.category,
    )
    return dest


@router.delete(
    "/destinations/{destination_id}",
    response_model=DestinationDeleteResponse,
)
async def remove_destination(
    destination_id: str,
    redis: RedisClientDep,
) -> DestinationDeleteResponse:
    """
    Remove a cycle route destination.

    Implements [cycle-route-assessment:DestinationManagement/TS-03]
    """
    deleted = await delete_destination(redis, destination_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "destination_not_found",
                    "message": f"No destination found with ID {destination_id}",
                }
            },
        )
    return DestinationDeleteResponse(deleted=True, destination_id=destination_id)
