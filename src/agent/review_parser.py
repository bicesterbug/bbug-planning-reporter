"""
Markdown parser for extracting structured data from review output.

Implements [review-output-fixes:FR-003] - Parse aspects from markdown
Implements [review-output-fixes:FR-004] - Parse policy compliance from markdown
Implements [review-output-fixes:FR-005] - Parse recommendations from markdown
Implements [review-output-fixes:FR-006] - Parse suggested conditions from markdown
Implements [review-output-fixes:NFR-001] - Parsing failures must not break review generation

Implements:
- [review-output-fixes:ReviewMarkdownParser/TS-01] Parse aspects table
- [review-output-fixes:ReviewMarkdownParser/TS-02] Parse aspects with varied ratings
- [review-output-fixes:ReviewMarkdownParser/TS-03] Parse aspects missing table
- [review-output-fixes:ReviewMarkdownParser/TS-04] Parse policy compliance
- [review-output-fixes:ReviewMarkdownParser/TS-05] Parse compliance emoji indicators
- [review-output-fixes:ReviewMarkdownParser/TS-06] Parse compliance missing table
- [review-output-fixes:ReviewMarkdownParser/TS-07] Parse recommendations
- [review-output-fixes:ReviewMarkdownParser/TS-08] Parse recommendations missing section
- [review-output-fixes:ReviewMarkdownParser/TS-09] Parse suggested conditions standalone
- [review-output-fixes:ReviewMarkdownParser/TS-10] Parse suggested conditions absent
- [review-output-fixes:ReviewMarkdownParser/TS-11] Parse real review output
- [review-output-fixes:ReviewMarkdownParser/TS-12] Whitespace tolerance
"""

import re

import structlog

logger = structlog.get_logger(__name__)


class ReviewMarkdownParser:
    """
    Stateless utility that extracts structured data from review markdown text.

    Each method returns parsed data or None if the section is not found.
    All parsing is wrapped in try/except for robustness — a parsing failure
    never causes a review to fail.
    """

    def parse_aspects(self, markdown: str) -> list[dict] | None:
        """
        Extract aspects from the Assessment Summary table.

        Looks for a markdown table under a heading containing "Assessment Summary"
        with columns: Aspect | Rating | Key Issue.

        Returns list of dicts with keys: name, rating (lowercased), key_issue.
        Returns None if the section is not found or cannot be parsed.
        """
        try:
            # Find the Assessment Summary section
            section = self._find_section(markdown, r"Assessment\s+Summary")
            if not section:
                return None

            # Find the table within the section
            rows = self._parse_table(section)
            if not rows:
                return None

            # Expect columns: Aspect, Rating, Key Issue
            aspects = []
            for row in rows:
                if len(row) < 3:
                    continue
                name = row[0].strip()
                rating = row[1].strip().lower()
                key_issue = row[2].strip() if len(row) > 2 else None

                if not name or not rating:
                    continue

                aspects.append({
                    "name": name,
                    "rating": rating,
                    "key_issue": key_issue or None,
                })

            return aspects if aspects else None

        except Exception as e:
            logger.warning("Failed to parse aspects", error=str(e))
            return None

    def parse_policy_compliance(self, markdown: str) -> list[dict] | None:
        """
        Extract policy compliance items from the Policy Compliance Matrix.

        Looks for a markdown table under a heading containing "Policy Compliance"
        with columns: Requirement | Policy Source | Compliant? | Notes.

        Compliance indicators are parsed from emoji prefixes:
        - "✅ YES" or "✅" → compliant=True
        - "❌ NO" or "❌" → compliant=False
        - "⚠️ PARTIAL" → compliant=False, notes includes "partial"
        - "⚠️ UNCLEAR" → compliant=False, notes includes "unclear"

        Returns list of dicts with keys: requirement, policy_source, compliant (bool), notes.
        Returns None if the section is not found or cannot be parsed.
        """
        try:
            section = self._find_section(markdown, r"Policy\s+Compliance(?:\s+Matrix)?")
            if not section:
                return None

            rows = self._parse_table(section)
            if not rows:
                return None

            items = []
            for row in rows:
                if len(row) < 3:
                    continue

                requirement = row[0].strip()
                policy_source = row[1].strip()
                compliance_text = row[2].strip()
                notes = row[3].strip() if len(row) > 3 else None

                if not requirement or not policy_source:
                    continue

                compliant, compliance_notes = self._parse_compliance_indicator(
                    compliance_text
                )

                # Merge compliance notes into notes field
                if compliance_notes:
                    if notes:
                        notes = f"{compliance_notes}; {notes}"
                    else:
                        notes = compliance_notes

                items.append({
                    "requirement": requirement,
                    "policy_source": policy_source,
                    "compliant": compliant,
                    "notes": notes or None,
                })

            return items if items else None

        except Exception as e:
            logger.warning("Failed to parse policy compliance", error=str(e))
            return None

    def parse_recommendations(self, markdown: str) -> list[str] | None:
        """
        Extract recommendation titles from the Recommendations section.

        Looks for numbered bold items like:
        1. **A41 Cycle Route to Bicester**

        Returns list of recommendation title strings.
        Returns None if the section is not found or cannot be parsed.
        """
        try:
            section = self._find_section(markdown, r"Recommendations")
            if not section:
                return None

            # Match numbered items with bold titles: "1. **Title**" or "1. **Title**\n"
            pattern = re.compile(
                r"^\s*\d+\.\s+\*\*(.+?)\*\*",
                re.MULTILINE,
            )
            matches = pattern.findall(section)

            if not matches:
                return None

            return [m.strip() for m in matches if m.strip()]

        except Exception as e:
            logger.warning("Failed to parse recommendations", error=str(e))
            return None

    def parse_suggested_conditions(self, markdown: str) -> list[str] | None:
        """
        Extract conditions from the Suggested Conditions section.

        Looks for numbered items under a heading containing "Suggested Conditions".
        Supports both bold and plain numbered items.

        Returns list of condition strings.
        Returns None if the section is not found or cannot be parsed.
        """
        try:
            section = self._find_section(markdown, r"Suggested\s+Conditions")
            if not section:
                return None

            # Match numbered items: "1. condition text" or "1. **condition text**"
            pattern = re.compile(
                r"^\s*\d+\.\s+(?:\*\*)?(.+?)(?:\*\*)?\s*$",
                re.MULTILINE,
            )
            matches = pattern.findall(section)

            if not matches:
                return None

            return [m.strip() for m in matches if m.strip()]

        except Exception as e:
            logger.warning("Failed to parse suggested conditions", error=str(e))
            return None

    def _find_section(self, markdown: str, heading_pattern: str) -> str | None:
        """
        Find a markdown section by heading pattern.

        Returns the text from the heading to the next same-level or higher heading,
        or to the end of the document.
        """
        # Match ## or ### headings containing the pattern
        pattern = re.compile(
            rf"^(#{{2,3}})\s+.*?{heading_pattern}.*$",
            re.MULTILINE | re.IGNORECASE,
        )
        match = pattern.search(markdown)
        if not match:
            return None

        heading_level = len(match.group(1))
        start = match.end()

        # Find the next heading at same or higher level
        next_heading = re.compile(
            rf"^#{{{1},{heading_level}}}\s+",
            re.MULTILINE,
        )
        next_match = next_heading.search(markdown, start)

        if next_match:
            return markdown[start:next_match.start()]
        return markdown[start:]

    def _parse_table(self, text: str) -> list[list[str]] | None:
        """
        Parse a markdown table from text, returning data rows (excluding header and separator).
        """
        lines = text.strip().split("\n")
        table_lines = []
        in_table = False

        for line in lines:
            stripped = line.strip()
            if "|" in stripped:
                in_table = True
                table_lines.append(stripped)
            elif in_table:
                # End of table
                break

        if len(table_lines) < 3:
            # Need at least header, separator, and one data row
            return None

        # Skip header (line 0) and separator (line 1)
        data_rows = []
        for line in table_lines[2:]:
            cells = [c.strip() for c in line.split("|")]
            # Remove empty first/last cells from leading/trailing pipes
            if cells and cells[0] == "":
                cells = cells[1:]
            if cells and cells[-1] == "":
                cells = cells[:-1]
            if cells:
                data_rows.append(cells)

        return data_rows if data_rows else None

    def _parse_compliance_indicator(self, text: str) -> tuple[bool, str | None]:
        """
        Parse a compliance indicator string.

        Returns (compliant: bool, extra_notes: str | None).
        """
        text_upper = text.upper().strip()

        # Check for emoji-prefixed indicators
        if "YES" in text_upper and ("✅" in text or "YES" in text_upper):
            return (True, None)

        if "PARTIAL" in text_upper:
            return (False, "Partial compliance")

        if "UNCLEAR" in text_upper:
            return (False, "Compliance unclear")

        if "NO" in text_upper and ("❌" in text or "NO" in text_upper):
            return (False, None)

        # Fallback: if contains check mark emoji, True; otherwise False
        if "✅" in text:
            return (True, None)
        if "❌" in text or "⚠️" in text:
            return (False, None)

        # Default to False for unrecognised indicators
        return (False, f"Unrecognised compliance indicator: {text}")
