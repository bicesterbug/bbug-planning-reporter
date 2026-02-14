"""
Structure call prompt builder for the two-phase review generation.

Implements [structured-review-output:FR-001] - Defines the structure call prompt
Implements [reliable-structure-extraction:FR-005] - Prompt updated for tool use
Implements [reliable-structure-extraction:FR-003] - Flexible aspect selection

Implements:
- [structured-review-output:StructureCallPrompt/TS-02] Evidence included
- [structured-review-output:StructureCallPrompt/TS-03] Document metadata included
- [reliable-structure-extraction:StructurePrompt/TS-01] No JSON-only instruction
- [reliable-structure-extraction:StructurePrompt/TS-02] No inline schema
- [reliable-structure-extraction:StructurePrompt/TS-03] Tool reference
- [reliable-structure-extraction:StructurePrompt/TS-04] Flexible aspects
- [reliable-structure-extraction:StructurePrompt/TS-05] Field guidance retained
- [reliable-structure-extraction:StructurePrompt/TS-06] Route evidence in user prompt
"""


def build_structure_prompt(
    app_summary: str,
    ingested_docs_text: str,
    app_evidence_text: str,
    policy_evidence_text: str,
    plans_submitted_text: str = "No plans or drawings were detected.",
    route_evidence_text: str = "No cycling route assessments were performed.",
) -> tuple[str, str]:
    """
    Build the system and user prompts for the structure call.

    The structure call asks Claude to return structured assessment data
    via the submit_review_structure tool. No markdown prose is requested.

    Args:
        app_summary: Formatted application metadata text.
        ingested_docs_text: Formatted list of ingested documents with metadata.
        app_evidence_text: Evidence chunks from application documents.
        policy_evidence_text: Evidence chunks from policy documents.
        plans_submitted_text: Formatted list of image-based documents that were
            downloaded but not ingested (plans, elevations, drawings).
        route_evidence_text: Cycling route assessment data with LTN 1/20 scores and issues.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    # Implements [reliable-structure-extraction:FR-005] - No inline JSON schema,
    # no "respond with JSON only" instruction. Tool definition provides the schema.
    system_prompt = """You are a planning application reviewer acting on behalf of a local cycling advocacy group in the Cherwell District. Your role is to assess planning applications from the perspective of people who walk and cycle.

Use the submit_review_structure tool to return your structured assessment.

Field guidance:

**overall_rating**: Your overall assessment of the application from a cycling advocacy perspective.
- "red" = Serious deficiencies requiring objection
- "amber" = Concerns that need addressing but could be resolved with conditions
- "green" = Acceptable provision for cycling and walking

**summary**: A concise 2-4 sentence summary of the review including the overall rating. This should capture the key finding, main concerns, and overall recommendation. Do not use markdown formatting.

**aspects**: Include the aspects that are relevant to the application under review. Consider these standard aspects but select only those applicable:
- "Cycle Parking" — quantity, type, location, security, accessibility
- "Cycle Routes" — on-site and connections to existing cycle network
- "Junctions" — junction design safety for cyclists, LTN 1/20 compliance
- "Permeability" — pedestrian/cycle permeability and filtered permeability
- "Policy Compliance" — overall policy compliance assessment

You may include additional aspects if the application warrants them (e.g. "Construction Phase Impacts", "Sustainable Transport"). Include at least one aspect.

Each aspect must have:
- "name": A descriptive name for the aspect
- "rating": red/amber/green for this aspect
- "key_issue": A brief (1 sentence) summary of the main issue
- "analysis": A detailed markdown-formatted analysis (2-4 paragraphs) covering findings, evidence references, and policy citations. This will be used by a report writer to produce detailed prose.

**policy_compliance**: List specific policy requirements checked. Include at least 8 items covering NPPF, LTN 1/20, Cherwell Local Plan, LCWIP, and Manual for Streets where relevant. Each item:
- "requirement": What the policy requires
- "policy_source": Specific policy and paragraph/section reference
- "compliant": true/false (use false for partial compliance)
- "notes": Brief explanation, or null

**recommendations**: List of specific, actionable recommendations with policy justification. Each string should be a complete recommendation.

**suggested_conditions**: Planning conditions to attach if approval is granted. May be an empty array if no conditions are warranted.

**key_documents**: Documents from the ingested documents list that are relevant to the cycling review. Select the most relevant documents. Each item:
- "title": Document title matching the ingested documents list
- "category": One of "Transport & Access", "Design & Layout", "Application Core"
- "summary": 1-2 sentences on content and cycling relevance
- "url": Download URL from the ingested documents list, or null
"""

    user_prompt = f"""Assess the following planning application from a cycling advocacy perspective using the submit_review_structure tool.

## Application Details
{app_summary}

## Ingested Documents
The following documents were downloaded from the planning portal. Use this list to populate the key_documents field:
{ingested_docs_text}

## Plans & Drawings Submitted
The following documents were identified as image-based (plans, elevations, drawings) and were downloaded but not text-searchable. Note their existence in your assessment where relevant:
{plans_submitted_text}

## Evidence from Application Documents
{app_evidence_text}

## Relevant Policy Extracts
{policy_evidence_text}

## Cycling Route Assessments
The following cycling route assessments were performed using LTN 1/20 criteria, scoring infrastructure quality from the development site to key destinations. Incorporate these findings into your cycle routes aspect assessment, recommendations, and S106 suggestions:
{route_evidence_text}

If the application documents don't contain enough transport/cycling information, note this in your analysis and base your assessment on what is available."""

    return system_prompt, user_prompt
