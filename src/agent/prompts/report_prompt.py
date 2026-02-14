"""
Report call prompt builder for the two-phase review generation.

Implements [structured-review-output:FR-001] - Defines the report call prompt
Implements [structured-review-output:FR-003] - Prompt constrains Claude to use JSON data
Implements [structured-review-output:FR-006] - Prompt specifies the report section structure

Implements:
- [structured-review-output:ReportCallPrompt/TS-01] JSON embedded in prompt
- [structured-review-output:ReportCallPrompt/TS-02] Report format specified
- [structured-review-output:ReportCallPrompt/TS-03] Binding language
"""


def build_report_prompt(
    structure_json: str,
    app_summary: str,
    ingested_docs_text: str,
    app_evidence_text: str,
    policy_evidence_text: str,
    plans_submitted_text: str = "No plans or drawings were detected.",
) -> tuple[str, str]:
    """
    Build the system and user prompts for the report call.

    The report call asks Claude to write a detailed prose markdown report
    using the structure call JSON as an authoritative outline.

    Args:
        structure_json: The validated JSON string from the structure call.
        app_summary: Formatted application metadata text.
        ingested_docs_text: Formatted list of ingested documents with metadata.
        app_evidence_text: Evidence chunks from application documents.
        policy_evidence_text: Evidence chunks from policy documents.
        plans_submitted_text: Formatted list of image-based documents that were
            downloaded but not ingested (plans, elevations, drawings).

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    system_prompt = """You are a planning application reviewer writing a detailed cycle advocacy review report. You have already completed a structured assessment (provided as JSON). Your task is to write the full prose markdown report.

## CRITICAL RULES

You MUST use the structured JSON assessment as your authoritative source:
- You MUST use the EXACT ratings from the JSON in the Assessment Summary table
- You MUST use the EXACT key issues from the JSON in the Assessment Summary table
- You MUST use the EXACT compliance verdicts (compliant: true/false) from the JSON in the Policy Compliance Matrix
- You MUST use the EXACT policy sources from the JSON in the Policy Compliance Matrix
- You MUST list the EXACT recommendations from the JSON in the Recommendations section
- You MUST list the EXACT suggested conditions from the JSON in the Suggested Conditions section
- You MUST list the EXACT key documents from the JSON in the Key Documents section
- You MUST NOT add aspects, compliance items, recommendations, or conditions that are not in the JSON
- You MUST NOT omit any aspects, compliance items, recommendations, or conditions that are in the JSON

## Report Structure

Write the report in this exact markdown format, in this exact order:

### 1. Title
```
# Cycle Advocacy Review: [Application Reference]
```

### 2. Application Summary
```
## Application Summary
- **Reference:** [ref]
- **Site:** [address]
- **Proposal:** [description]
- **Applicant:** [name]
- **Status:** [status]
```

### 3. Key Documents
```
## Key Documents
```
Group the key documents from the JSON by category. For each category, list documents as markdown links with summaries:
```
### Transport & Access
- [Document Title](url)
  Summary from JSON...
```
For documents with null URL, render without a link. Order categories: Transport & Access, Design & Layout, Application Core.

### 4. Assessment Summary
```
## Assessment Summary
**Overall Rating:** RED/AMBER/GREEN (from JSON overall_rating, uppercase)

| Aspect | Rating | Key Issue |
|--------|--------|-----------|
```
One row per aspect from the JSON, with Rating in UPPERCASE.

### 5. Detailed Assessment
```
## Detailed Assessment
```
One subsection per aspect from the JSON. Use the `analysis` field from each aspect as your starting point and expand it into detailed prose (2-5 paragraphs per aspect). Reference specific evidence and policy requirements. Number the subsections:
```
### 1. Cycle Parking
[Expanded prose based on analysis field]

### 2. Cycle Route Provision
[Expanded prose]
```

### 6. Policy Compliance Matrix
```
## Policy Compliance Matrix

| Requirement | Policy Source | Compliant? | Notes |
|---|---|---|---|
```
One row per compliance item from the JSON. Render compliant as "YES" or "NO".

### 7. Recommendations
```
## Recommendations
```
Numbered list. Each recommendation from the JSON as a numbered item with policy justification.

### 8. Suggested Conditions
```
## Suggested Conditions
```
If the JSON has suggested conditions, list them numbered. If the array is empty, write "No specific conditions recommended beyond standard requirements." or omit the section."""

    user_prompt = f"""Write a detailed cycle advocacy review report based on the structured assessment below.

## Structured Assessment (JSON — authoritative source)
```json
{structure_json}
```

## Application Details
{app_summary}

## Ingested Documents
{ingested_docs_text}

## Plans & Drawings Submitted
The following documents were identified as image-based (plans, elevations, drawings) and were downloaded but not text-searchable. Reference their existence where relevant:
{plans_submitted_text}

## Evidence from Application Documents
{app_evidence_text}

## Relevant Policy Extracts
{policy_evidence_text}

Write the full markdown report following the format specified in your instructions. Expand the analysis notes into detailed prose for each aspect. The report should be suitable for submission as a formal consultation response."""

    return system_prompt, user_prompt
