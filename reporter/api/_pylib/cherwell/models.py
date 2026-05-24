"""
Data models for Cherwell Scraper output.

Implements [foundation-api:FR-009] - ApplicationMetadata structure
Implements [foundation-api:FR-010] - DocumentInfo structure
"""

from dataclasses import dataclass
from datetime import date


@dataclass
class ApplicationMetadata:
    """
    Structured metadata for a planning application.

    Implements [foundation-api:ApplicationMetadata/TS-01] - Valid application metadata
    Implements [foundation-api:ApplicationMetadata/TS-02] - Optional date handling
    """

    reference: str
    """Application reference (e.g., '25/01178/REM')"""

    address: str | None = None
    """Site address"""

    proposal: str | None = None
    """Development proposal description"""

    applicant: str | None = None
    """Applicant name"""

    agent: str | None = None
    """Agent name (if different from applicant)"""

    status: str | None = None
    """Current application status"""

    application_type: str | None = None
    """Type of planning application"""

    ward: str | None = None
    """Electoral ward"""

    parish: str | None = None
    """Parish council area"""

    date_received: date | None = None
    """Date application was received"""

    date_validated: date | None = None
    """Date application was validated"""

    target_date: date | None = None
    """Target decision date"""

    decision_date: date | None = None
    """Actual decision date (if decided)"""

    decision: str | None = None
    """Decision outcome (if decided)"""

    case_officer: str | None = None
    """Assigned case officer"""

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "reference": self.reference,
            "address": self.address,
            "proposal": self.proposal,
            "applicant": self.applicant,
            "agent": self.agent,
            "status": self.status,
            "application_type": self.application_type,
            "ward": self.ward,
            "parish": self.parish,
            "date_received": self.date_received.isoformat() if self.date_received else None,
            "date_validated": self.date_validated.isoformat() if self.date_validated else None,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "decision_date": self.decision_date.isoformat() if self.decision_date else None,
            "decision": self.decision,
            "case_officer": self.case_officer,
        }


@dataclass
class DocumentInfo:
    """
    Information about a planning application document.

    Implements [foundation-api:FR-010] - DocumentInfo structure
    """

    document_id: str
    """Unique identifier for the document"""

    description: str
    """Document description/title"""

    document_type: str | None = None
    """Type/category of document"""

    date_published: date | None = None
    """Date document was published"""

    url: str | None = None
    """Download URL for the document"""

    file_size: int | None = None
    """File size in bytes (if known)"""

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "document_id": self.document_id,
            "description": self.description,
            "document_type": self.document_type,
            "date_published": self.date_published.isoformat() if self.date_published else None,
            "url": self.url,
            "file_size": self.file_size,
        }


@dataclass
class DownloadResult:
    """Result of downloading a document."""

    document_id: str
    """Document that was downloaded"""

    file_path: str
    """Local path where document was saved"""

    file_size: int
    """Size of downloaded file in bytes"""

    success: bool = True
    """Whether download succeeded"""

    error: str | None = None
    """Error message if download failed"""

    # Implements [key-documents:FR-001] - Source metadata for key documents listing
    description: str | None = None
    """Document description/title from portal"""

    document_type: str | None = None
    """Document type/category from portal"""

    url: str | None = None
    """Original download URL from portal"""

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "document_id": self.document_id,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "success": self.success,
            "error": self.error,
            "description": self.description,
            "document_type": self.document_type,
            "url": self.url,
        }
