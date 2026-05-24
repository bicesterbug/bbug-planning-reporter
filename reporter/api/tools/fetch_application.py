"""Custom tool: fetch_application (Vercel Python function).

Fetches a Cherwell planning application from the public planning register and
returns COMPACT JSON only — application metadata, a document manifest, and
missing-document flags. Raw HTML never leaves this function; the agent never
sees portal markup.

"MCPs/tools do, skills know": this is deterministic acquisition. The judgement
(is the assessment adequate?) belongs to the agent + skills.

Reuses the proven Cherwell HTML parser ported verbatim into api/_pylib/cherwell.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

import httpx

# Make api/_pylib importable (bundled via vercel.json includeFiles).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _pylib.cherwell.parsers import CherwellParser  # noqa: E402

PORTAL_URL = os.getenv("CHERWELL_PORTAL_URL", "https://planningregister.cherwell.gov.uk")
USER_AGENT = os.getenv("SCRAPER_USER_AGENT", "BBUG-Planning-Reporter/2.0 (+cycling advocacy)")
INTERNAL_TOKEN = os.getenv("INTERNAL_TOOL_TOKEN", "")

# Validation trigger heuristics — when these document types are expected but
# absent, the agent should question why (advocate framing, not officer framing).
# Full thresholds live in the planning-application-search skill's reference table.
_TRANSPORT_DOC_HINTS = ("transport assessment", "transport statement", "transport report")
_TRAVEL_PLAN_HINTS = ("travel plan", "framework travel plan")


def _display_url(reference: str) -> str:
    return f"{PORTAL_URL}/Planning/Display/{reference}"


def _missing_document_flags(proposal: str | None, documents: list[dict]) -> list[str]:
    """Cheap, deterministic gap flags. The agent decides what they mean."""
    flags: list[str] = []
    descriptions = " ".join((d.get("description") or "").lower() for d in documents)
    types = " ".join((d.get("document_type") or "").lower() for d in documents)
    haystack = f"{descriptions} {types}"

    has_transport = any(h in haystack for h in _TRANSPORT_DOC_HINTS)
    has_travel_plan = any(h in haystack for h in _TRAVEL_PLAN_HINTS)

    proposal_l = (proposal or "").lower()
    looks_residential = any(w in proposal_l for w in ("dwelling", "dwellings", "houses", "homes", "residential"))
    looks_major = any(w in proposal_l for w in ("erection of", "development", "outline", "reserved matters"))

    if (looks_residential or looks_major) and not has_transport:
        flags.append("no_transport_assessment_or_statement")
    if looks_residential and not has_travel_plan:
        flags.append("no_travel_plan")
    return flags


def _fetch(reference: str) -> dict:
    parser = CherwellParser()
    headers = {"User-Agent": USER_AGENT}
    url = _display_url(reference)

    with httpx.Client(timeout=30.0, headers=headers, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        html = resp.text

    meta = parser.parse_application_details(html, reference)
    documents = parser.parse_document_list(html, reference, base_url=PORTAL_URL)

    doc_manifest = [
        {
            "document_id": d.document_id,
            "description": d.description,
            "document_type": d.document_type,
            "date_published": d.date_published.isoformat() if d.date_published else None,
            "url": d.url,
            "file_size": d.file_size,
        }
        for d in documents
    ]

    return {
        "reference": meta.reference,
        "address": meta.address,
        "proposal": meta.proposal,
        "applicant": meta.applicant,
        "agent": meta.agent,
        "status": meta.status,
        "application_type": meta.application_type,
        "ward": meta.ward,
        "parish": meta.parish,
        "date_received": meta.date_received.isoformat() if meta.date_received else None,
        "date_validated": meta.date_validated.isoformat() if meta.date_validated else None,
        "target_date": meta.target_date.isoformat() if meta.target_date else None,
        "decision_date": meta.decision_date.isoformat() if meta.decision_date else None,
        "decision": meta.decision,
        "case_officer": meta.case_officer,
        "document_count": len(doc_manifest),
        "documents": doc_manifest,
        "missing_document_flags": _missing_document_flags(meta.proposal, doc_manifest),
        "source_url": url,
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (Vercel Python entrypoint name)
        if INTERNAL_TOKEN and self.headers.get("x-internal-token") != INTERNAL_TOKEN:
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            reference = (body.get("application_ref") or "").strip()
            if not reference:
                self._send(400, {"error": "application_ref is required"})
                return
            self._send(200, _fetch(reference))
        except httpx.HTTPStatusError as exc:
            self._send(502, {"error": f"portal returned {exc.response.status_code}", "reference": reference})
        except Exception as exc:  # noqa: BLE001 — return structured error to the agent
            self._send(500, {"error": str(exc)})

    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
