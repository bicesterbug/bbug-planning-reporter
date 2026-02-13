"""
Cherwell Scraper MCP Server.

Implements [foundation-api:FR-009] - Scrape application metadata
Implements [foundation-api:FR-010] - List application documents
Implements [foundation-api:FR-011] - Download documents
Implements [foundation-api:FR-012] - Polite scraping

Implements:
- [foundation-api:CherwellScraperMCP/TS-01] Fetch application details
- [foundation-api:CherwellScraperMCP/TS-02] Non-existent application
- [foundation-api:CherwellScraperMCP/TS-03] List documents
- [foundation-api:CherwellScraperMCP/TS-04] Download single document
- [foundation-api:CherwellScraperMCP/TS-05] Download all documents
- [foundation-api:CherwellScraperMCP/TS-06] Rate limiting
- [foundation-api:CherwellScraperMCP/TS-07] Transient error retry
- [foundation-api:CherwellScraperMCP/TS-08] Paginated document list
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.routing import Mount, Route

from src.mcp_servers.cherwell_scraper.client import (
    ApplicationNotFoundError,
    CherwellClient,
    CherwellClientError,
)
from src.mcp_servers.cherwell_scraper.filters import DocumentFilter
from src.mcp_servers.cherwell_scraper.models import DownloadResult
from src.mcp_servers.cherwell_scraper.parsers import CherwellParser

logger = structlog.get_logger(__name__)


# Tool input schemas
class GetApplicationDetailsInput(BaseModel):
    """Input schema for get_application_details tool."""

    application_ref: str = Field(
        description="Planning application reference (e.g., '25/01178/REM')"
    )


class ListApplicationDocumentsInput(BaseModel):
    """Input schema for list_application_documents tool."""

    application_ref: str = Field(
        description="Planning application reference (e.g., '25/01178/REM')"
    )


class DownloadDocumentInput(BaseModel):
    """Input schema for download_document tool."""

    document_url: str = Field(description="URL of the document to download")
    output_dir: str = Field(description="Directory to save the document")
    filename: str | None = Field(
        default=None, description="Optional filename (defaults to URL-derived name)"
    )


class DownloadAllDocumentsInput(BaseModel):
    """
    Input schema for download_all_documents tool.

    Implements [document-filtering:FR-005] - Override filter with skip_filter flag
    Implements [document-filtering:FR-007] - Default filtering enabled
    """

    application_ref: str = Field(
        description="Planning application reference (e.g., '25/01178/REM')"
    )
    output_dir: str = Field(
        description="Base directory to save documents (will create ref subdirectory)"
    )
    skip_filter: bool = Field(
        default=False,
        description="Bypass document filtering and download all documents (default: False)",
    )
    # Implements [review-scope-control:FR-004] - Toggle fields on MCP tool input
    include_consultation_responses: bool = Field(
        default=False,
        description="Include consultation responses in download (default: False)",
    )
    include_public_comments: bool = Field(
        default=False,
        description="Include public comments in download (default: False)",
    )
    selected_document_ids: list[str] | None = Field(
        default=None,
        description=(
            "When provided, only download documents whose document_id is in "
            "this list. Documents are still subject to the pattern-based "
            "filter unless skip_filter is True. Pass this after an LLM-based "
            "relevance filter to avoid downloading hundreds of irrelevant "
            "documents."
        ),
    )


class CherwellScraperMCP:
    """
    MCP server for scraping Cherwell planning portal.

    Provides tools to:
    - Get application details (metadata)
    - List application documents
    - Download documents
    """

    def __init__(
        self,
        portal_url: str | None = None,
        rate_limit: float | None = None,
    ) -> None:
        """
        Initialize the Cherwell Scraper MCP server.

        Args:
            portal_url: Base URL of the Cherwell planning portal.
            rate_limit: Minimum seconds between requests.
        """
        self._portal_url = portal_url
        self._rate_limit = rate_limit
        self._parser = CherwellParser()

        # MCP server
        self._server = Server("cherwell-scraper-mcp")
        self._setup_handlers()

    def _get_client(self) -> CherwellClient:
        """Create a new client instance for each request context."""
        return CherwellClient(
            base_url=self._portal_url,
            rate_limit=self._rate_limit,
        )

    def _setup_handlers(self) -> None:
        """Set up MCP server handlers."""

        @self._server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available tools."""
            return [
                Tool(
                    name="get_application_details",
                    description="Get structured metadata for a planning application including address, proposal, status, and dates.",
                    inputSchema=GetApplicationDetailsInput.model_json_schema(),
                ),
                Tool(
                    name="list_application_documents",
                    description="List all documents associated with a planning application, including document types and download URLs.",
                    inputSchema=ListApplicationDocumentsInput.model_json_schema(),
                ),
                Tool(
                    name="download_document",
                    description="Download a single document to local storage.",
                    inputSchema=DownloadDocumentInput.model_json_schema(),
                ),
                Tool(
                    name="download_all_documents",
                    description="Download all documents for a planning application to local storage.",
                    inputSchema=DownloadAllDocumentsInput.model_json_schema(),
                ),
            ]

        @self._server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            """Handle tool calls."""
            try:
                if name == "get_application_details":
                    result = await self._get_application_details(
                        GetApplicationDetailsInput(**arguments)
                    )
                elif name == "list_application_documents":
                    result = await self._list_application_documents(
                        ListApplicationDocumentsInput(**arguments)
                    )
                elif name == "download_document":
                    result = await self._download_document(
                        DownloadDocumentInput(**arguments)
                    )
                elif name == "download_all_documents":
                    result = await self._download_all_documents(
                        DownloadAllDocumentsInput(**arguments)
                    )
                else:
                    result = {"error": f"Unknown tool: {name}"}

                return [TextContent(type="text", text=json.dumps(result))]

            except ApplicationNotFoundError as e:
                logger.warning(
                    "Application not found",
                    tool=name,
                    error_code=e.error_code,
                )
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({
                            "status": "error",
                            "error_code": e.error_code,
                            "message": e.message,
                            "details": e.details,
                        }),
                    )
                ]
            except CherwellClientError as e:
                logger.error(
                    "Cherwell client error",
                    tool=name,
                    error_code=e.error_code,
                    error=e.message,
                )
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({
                            "status": "error",
                            "error_code": e.error_code,
                            "message": e.message,
                            "details": e.details,
                        }),
                    )
                ]
            except Exception as e:
                logger.exception("Tool call failed", tool=name, error=str(e))
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({
                            "status": "error",
                            "error_code": "internal_error",
                            "message": str(e),
                        }),
                    )
                ]

    async def _get_application_details(
        self, input: GetApplicationDetailsInput
    ) -> dict[str, Any]:
        """
        Get application metadata.

        Implements [foundation-api:CherwellScraperMCP/TS-01] - Fetch application details
        Implements [foundation-api:CherwellScraperMCP/TS-02] - Non-existent application
        """
        async with self._get_client() as client:
            html = await client.get_application_page(input.application_ref)
            metadata = self._parser.parse_application_details(html, input.application_ref)

            logger.info(
                "Retrieved application details",
                reference=input.application_ref,
                has_address=metadata.address is not None,
                status=metadata.status,
            )

            return {
                "status": "success",
                "application": metadata.to_dict(),
            }

    async def _fetch_all_document_info(
        self, application_ref: str
    ) -> list:
        """
        Fetch and parse all documents for an application.

        Internal helper that returns DocumentInfo objects for filtering.

        Args:
            application_ref: Planning application reference

        Returns:
            List of DocumentInfo objects
        """
        all_documents = []

        async with self._get_client() as client:
            # Fetch first page
            html = await client.get_documents_page(application_ref)
            documents = self._parser.parse_document_list(
                html, application_ref, client.base_url
            )
            all_documents.extend(documents)

            # Handle pagination
            next_url = self._parser.get_next_page_url(html, client.base_url)
            page = 2
            max_pages = 50  # Safety limit

            while next_url and page <= max_pages:
                html = await client.get_page(next_url)
                documents = self._parser.parse_document_list(
                    html, application_ref, client.base_url
                )
                if not documents:
                    break
                all_documents.extend(documents)
                next_url = self._parser.get_next_page_url(html, client.base_url)
                page += 1

            logger.info(
                "Retrieved document list",
                reference=application_ref,
                document_count=len(all_documents),
                pages_fetched=page - 1,
            )

        return all_documents

    async def _list_application_documents(
        self, input: ListApplicationDocumentsInput
    ) -> dict[str, Any]:
        """
        List all documents for an application.

        Implements [foundation-api:CherwellScraperMCP/TS-03] - List documents
        Implements [foundation-api:CherwellScraperMCP/TS-08] - Paginated document list
        """
        all_documents = await self._fetch_all_document_info(input.application_ref)

        return {
            "status": "success",
            "application_ref": input.application_ref,
            "document_count": len(all_documents),
            "documents": [doc.to_dict() for doc in all_documents],
        }

    async def _download_document(
        self, input: DownloadDocumentInput
    ) -> dict[str, Any]:
        """
        Download a single document.

        Implements [foundation-api:CherwellScraperMCP/TS-04] - Download single document
        """
        output_dir = Path(input.output_dir)

        # Derive filename from URL if not provided
        if input.filename:
            filename = input.filename
        else:
            # Extract filename from URL
            from urllib.parse import unquote, urlparse

            parsed = urlparse(input.document_url)
            filename = unquote(parsed.path.split("/")[-1])
            if not filename or filename == "":
                filename = f"document_{hash(input.document_url) & 0xFFFFFFFF}.pdf"

        output_path = output_dir / filename

        async with self._get_client() as client:
            file_size = await client.download_document(input.document_url, output_path)

            return {
                "status": "success",
                "file_path": str(output_path),
                "file_size": file_size,
            }

    async def _download_all_documents(
        self, input: DownloadAllDocumentsInput
    ) -> dict[str, Any]:
        """
        Download all documents for an application.

        Implements [foundation-api:CherwellScraperMCP/TS-05] - Download all documents
        Implements [document-filtering:FR-001] - Filter public comments
        Implements [document-filtering:FR-005] - Override filter with skip_filter flag
        Implements [document-filtering:FR-006] - Report filtered documents
        Implements [document-filtering:NFR-003] - Log filter decisions

        Implements:
        - [document-filtering:CherwellScraperMCP/TS-01] Filter public comments
        - [document-filtering:CherwellScraperMCP/TS-02] Override filter
        - [document-filtering:CherwellScraperMCP/TS-03] Unknown type defaults to download
        - [document-filtering:CherwellScraperMCP/TS-04] Response includes counts
        """
        # Create output directory for this application
        # Sanitize reference for use in path
        safe_ref = input.application_ref.replace("/", "_")
        output_dir = Path(input.output_dir) / safe_ref
        output_dir.mkdir(parents=True, exist_ok=True)

        # Fetch and parse document list to get DocumentInfo objects
        all_documents = await self._fetch_all_document_info(input.application_ref)

        if not all_documents:
            return {
                "status": "success",
                "application_ref": input.application_ref,
                "message": "No documents found for this application",
                "total_documents": 0,
                "downloaded_count": 0,
                "filtered_count": 0,
                "downloads": [],
                "filtered_documents": [],
            }

        # Pre-filter by selected_document_ids when provided (LLM filter stage)
        if input.selected_document_ids is not None:
            selected_set = set(input.selected_document_ids)
            all_documents = [
                doc for doc in all_documents if doc.document_id in selected_set
            ]
            logger.info(
                "Pre-filtered to selected document IDs",
                application_ref=input.application_ref,
                selected_count=len(all_documents),
                requested_ids=len(selected_set),
            )

        # Apply document filter
        # Implements [document-filtering:FR-001], [FR-002], [FR-003], [FR-004], [FR-005]
        # Implements [review-scope-control:FR-004] - Pass toggle flags to filter
        document_filter = DocumentFilter()
        documents_to_download, filtered_documents = document_filter.filter_documents(
            all_documents,
            skip_filter=input.skip_filter,
            application_ref=input.application_ref,
            include_consultation_responses=input.include_consultation_responses,
            include_public_comments=input.include_public_comments,
        )

        # Implements [document-filtering:NFR-003] - Log filtering summary
        logger.info(
            "Document filtering complete",
            application_ref=input.application_ref,
            total_documents=len(all_documents),
            to_download=len(documents_to_download),
            filtered=len(filtered_documents),
            skip_filter=input.skip_filter,
        )

        # Download allowed documents only
        downloads: list[dict] = []
        async with self._get_client() as client:
            for i, doc in enumerate(documents_to_download, 1):
                if not doc.url:
                    downloads.append(
                        DownloadResult(
                            document_id=doc.document_id,
                            file_path="",
                            file_size=0,
                            success=False,
                            error="No URL available",
                            description=doc.description,
                            document_type=doc.document_type,
                            url=None,
                        ).to_dict()
                    )
                    continue

                # Generate filename from description and index
                description = doc.description or f"document_{i}"
                # Sanitize filename
                safe_name = "".join(
                    c if c.isalnum() or c in "._- " else "_" for c in description
                )[:100]
                filename = f"{i:03d}_{safe_name}.pdf"
                output_path = output_dir / filename

                try:
                    file_size = await client.download_document(doc.url, output_path)
                    downloads.append(
                        DownloadResult(
                            document_id=doc.document_id,
                            file_path=str(output_path),
                            file_size=file_size,
                            success=True,
                            description=doc.description,
                            document_type=doc.document_type,
                            url=doc.url,
                        ).to_dict()
                    )
                except CherwellClientError as e:
                    logger.warning(
                        "Document download failed",
                        document_id=doc.document_id,
                        error=e.message,
                    )
                    downloads.append(
                        DownloadResult(
                            document_id=doc.document_id,
                            file_path=str(output_path),
                            file_size=0,
                            success=False,
                            error=e.message,
                            description=doc.description,
                            document_type=doc.document_type,
                            url=doc.url,
                        ).to_dict()
                    )

        successful = sum(1 for d in downloads if d.get("success"))
        logger.info(
            "Downloaded all documents",
            reference=input.application_ref,
            total=len(all_documents),
            downloaded=len(documents_to_download),
            filtered=len(filtered_documents),
            successful=successful,
            failed=len(downloads) - successful,
        )

        # Implements [document-filtering:FR-006] - Return filtered documents with reasons
        # Implements [document-filtering:CherwellScraperMCP/TS-04] - Response includes counts
        return {
            "status": "success",
            "application_ref": input.application_ref,
            "output_dir": str(output_dir),
            "total_documents": len(all_documents),
            "downloaded_count": len(documents_to_download),
            "filtered_count": len(filtered_documents),
            "successful_downloads": successful,
            "failed_downloads": len(downloads) - successful,
            "downloads": downloads,
            "filtered_documents": [f.to_dict() for f in filtered_documents],
        }

    @property
    def server(self) -> Server:
        """Get the MCP server instance."""
        return self._server


def create_app(
    portal_url: str | None = None,
    rate_limit: float | None = None,
) -> Starlette:
    """
    Create the Starlette application with SSE transport.

    Args:
        portal_url: Base URL of the Cherwell planning portal.
        rate_limit: Minimum seconds between requests.

    Returns:
        Configured Starlette application.
    """
    mcp_server = CherwellScraperMCP(
        portal_url=portal_url,
        rate_limit=rate_limit,
    )
    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp_server.server.run(
                streams[0], streams[1], mcp_server.server.create_initialization_options()
            )

    routes = [
        Route("/sse", endpoint=handle_sse),
        Mount("/messages", app=sse.handle_post_message),
    ]

    return Starlette(routes=routes)


async def main() -> None:
    """Run the MCP server."""
    import uvicorn

    # Configuration from environment
    portal_url = os.getenv("CHERWELL_PORTAL_URL", "https://planning.cherwell.gov.uk")
    rate_limit = float(os.getenv("SCRAPER_RATE_LIMIT", "1.0"))
    port = int(os.getenv("CHERWELL_SCRAPER_PORT", "3001"))

    logger.info(
        "Cherwell Scraper MCP Server starting",
        component="cherwell-scraper-mcp",
        portal_url=portal_url,
        rate_limit=rate_limit,
        port=port,
    )

    app = create_app(
        portal_url=portal_url,
        rate_limit=rate_limit,
    )

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
