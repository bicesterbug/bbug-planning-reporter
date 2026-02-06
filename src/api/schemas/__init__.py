"""
API Schemas package.

Re-exports all schemas for convenient imports.
"""

# Import policy schemas directly
# Re-export the original review schemas from schemas.py
# Note: schemas.py and schemas/ directory coexist
# We use importlib to load schemas.py directly
import importlib.util
import sys
from pathlib import Path

from src.api.schemas.policy import (
    SOURCE_SLUG_PATTERN,
    CreatePolicyRequest,
    CreateRevisionRequest,
    EffectivePolicySnapshot,
    EffectiveSnapshotResponse,
    PolicyCategory,
    PolicyDocumentDetail,
    PolicyDocumentRecord,
    PolicyDocumentSummary,
    PolicyListResponse,
    PolicyRevisionDetail,
    PolicyRevisionRecord,
    PolicyRevisionSummary,
    RevisionCreateResponse,
    RevisionDeleteResponse,
    RevisionStatus,
    RevisionStatusResponse,
    UpdatePolicyRequest,
    UpdateRevisionRequest,
)

# Load the schemas.py file directly
_schemas_file = Path(__file__).parent.parent / "schemas.py"
_spec = importlib.util.spec_from_file_location("_api_schemas_base", _schemas_file)
_module = importlib.util.module_from_spec(_spec)
sys.modules["_api_schemas_base"] = _module
_spec.loader.exec_module(_module)

# Re-export all public symbols from the base schemas
APPLICATION_REF_PATTERN = _module.APPLICATION_REF_PATTERN
ErrorDetail = _module.ErrorDetail
ErrorResponse = _module.ErrorResponse
ReviewLinks = _module.ReviewLinks
ReviewListResponse = _module.ReviewListResponse
ReviewProgressResponse = _module.ReviewProgressResponse
ReviewRequest = _module.ReviewRequest
ReviewResponse = _module.ReviewResponse
ReviewStatusResponse = _module.ReviewStatusResponse
ReviewSubmitResponse = _module.ReviewSubmitResponse
ReviewSummary = _module.ReviewSummary
WebhookConfigRequest = _module.WebhookConfigRequest
ReviewOptionsRequest = _module.ReviewOptionsRequest
ApplicationInfo = _module.ApplicationInfo
ReviewAspect = _module.ReviewAspect
PolicyCompliance = _module.PolicyCompliance
ReviewContent = _module.ReviewContent
PolicyRevisionUsed = _module.PolicyRevisionUsed
ReviewMetadata = _module.ReviewMetadata

__all__ = [
    # Review schemas (from schemas.py)
    "APPLICATION_REF_PATTERN",
    "ErrorDetail",
    "ErrorResponse",
    "ReviewLinks",
    "ReviewListResponse",
    "ReviewProgressResponse",
    "ReviewRequest",
    "ReviewResponse",
    "ReviewStatusResponse",
    "ReviewSubmitResponse",
    "ReviewSummary",
    "WebhookConfigRequest",
    "ReviewOptionsRequest",
    "ApplicationInfo",
    "ReviewAspect",
    "PolicyCompliance",
    "ReviewContent",
    "PolicyRevisionUsed",
    "ReviewMetadata",
    # Policy schemas
    "SOURCE_SLUG_PATTERN",
    "CreatePolicyRequest",
    "CreateRevisionRequest",
    "EffectivePolicySnapshot",
    "EffectiveSnapshotResponse",
    "PolicyCategory",
    "PolicyDocumentDetail",
    "PolicyDocumentRecord",
    "PolicyDocumentSummary",
    "PolicyListResponse",
    "PolicyRevisionDetail",
    "PolicyRevisionRecord",
    "PolicyRevisionSummary",
    "RevisionCreateResponse",
    "RevisionDeleteResponse",
    "RevisionStatus",
    "RevisionStatusResponse",
    "UpdatePolicyRequest",
    "UpdateRevisionRequest",
]
