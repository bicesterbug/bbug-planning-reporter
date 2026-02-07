"""
Letter prompt builder for consultee response letter generation.

Implements [response-letter:FR-002] - Stance-based framing
Implements [response-letter:FR-003] - Advocacy group identity in prompt
Implements [response-letter:FR-004] - Case officer addressing
Implements [response-letter:FR-005] - Inline policy citations with bibliography
Implements [response-letter:FR-007] - Tone selection
Implements [response-letter:FR-009] - Letter content structure (10 sections)
Implements [response-letter:FR-010] - Produces prompt pair for Claude API
"""

from datetime import date
from typing import Any


# Stance-specific framing instructions
_STANCE_INSTRUCTIONS = {
    "object": (
        "The group OBJECTS to this planning application. "
        "Frame the letter as a formal objection. The opening paragraph should clearly state "
        "that the group opposes the application. Body paragraphs should present the review "
        "findings as grounds for objection, using assertive language such as 'fails to', "
        "'is contrary to', 'does not comply with'. The closing paragraph should request "
        "that the application be refused."
    ),
    "support": (
        "The group SUPPORTS this planning application. "
        "Frame the letter as a letter of support. The opening paragraph should clearly state "
        "that the group welcomes the application. Body paragraphs should highlight positive "
        "aspects from the review, while noting any minor concerns. The closing paragraph "
        "should request that the application be approved."
    ),
    "conditional": (
        "The group supports this application SUBJECT TO CONDITIONS. "
        "Frame the letter as conditional support. The opening paragraph should state that "
        "the group supports the application in principle but has concerns that must be "
        "addressed through planning conditions. Body paragraphs should present both the "
        "positives and the issues requiring conditions. Include a dedicated 'Suggested "
        "Conditions' section listing specific conditions the group requests. The closing "
        "paragraph should request approval subject to the stated conditions."
    ),
    "neutral": (
        "The group is providing NEUTRAL COMMENTS on this application without taking a "
        "formal position for or against. Frame the letter as factual observations. The "
        "opening paragraph should state that the group wishes to draw the case officer's "
        "attention to certain matters. Body paragraphs should present the review findings "
        "as factual observations without advocating for or against approval. The closing "
        "paragraph should ask the case officer to take these comments into account."
    ),
}

# Tone-specific style instructions
_TONE_INSTRUCTIONS = {
    "formal": (
        "Use professional planning language with precise technical terminology. "
        "Write in a formal register appropriate for correspondence with a planning "
        "authority. Use terms such as 'provision', 'permeability', 'segregated cycle "
        "infrastructure', 'modal split', and 'design standards'. Reference policy "
        "documents by their full titles and specific paragraph/section numbers."
    ),
    "accessible": (
        "Use clear, jargon-light language that is accessible to councillors and the "
        "general public. Avoid overly technical planning terminology where a simpler "
        "phrase would suffice. For example, prefer 'safe cycling routes' over "
        "'segregated cycle infrastructure', and 'walking and cycling connections' over "
        "'filtered permeability'. Still reference policy documents but explain their "
        "significance in plain terms."
    ),
}


def build_letter_prompt(
    review_result: dict[str, Any],
    stance: str,
    tone: str,
    group_name: str,
    group_stylised: str,
    group_short: str,
    case_officer: str | None = None,
    letter_date: date | None = None,
    policy_revisions: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """
    Build the system and user prompts for letter generation.

    Implements [response-letter:LetterPrompt/TS-01] through [TS-09]

    Args:
        review_result: The completed review result dict from Redis.
        stance: One of object, support, conditional, neutral.
        tone: One of formal, accessible.
        group_name: Full group name (e.g. "Bicester Bike Users' Group").
        group_stylised: Stylised name (e.g. "Bicester BUG").
        group_short: Abbreviation (e.g. "BBUG").
        case_officer: Case officer name, or None for generic addressing.
        letter_date: Date for the letter, or None for today.
        policy_revisions: List of policy revision dicts used in the review.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    system_prompt = _build_system_prompt(stance, tone, group_name, group_stylised, group_short)
    user_prompt = _build_user_prompt(
        review_result, stance, group_stylised, group_short,
        case_officer, letter_date, policy_revisions,
    )
    return system_prompt, user_prompt


def _build_system_prompt(
    stance: str,
    tone: str,
    group_name: str,
    group_stylised: str,
    group_short: str,
) -> str:
    """Build the system prompt with stance framing and tone instructions."""
    stance_instruction = _STANCE_INSTRUCTIONS.get(stance, _STANCE_INSTRUCTIONS["neutral"])
    tone_instruction = _TONE_INSTRUCTIONS.get(tone, _TONE_INSTRUCTIONS["formal"])

    return f"""You are a letter writer for {group_stylised} ({group_short}), a local cycling \
advocacy group. Your task is to convert a planning application review into a formal \
consultee response letter addressed to the planning authority.

IMPORTANT: You are writing a LETTER, not a review. The voice is that of {group_stylised} \
writing to the planning case officer. Do not reproduce the review format — rewrite the \
content as persuasive, structured letter prose.

## Group Identity
- Full name: {group_name}
- Stylised name: {group_stylised}
- Abbreviation: {group_short}

## Stance
{stance_instruction}

## Tone
{tone_instruction}

## Required Letter Sections
The letter MUST include ALL of the following sections in this order:

1. **Sender header** — {group_stylised} name and the letter date
2. **Recipient addressing** — The case officer name (or "Dear Sir/Madam") and \
"Cherwell District Council, Planning Department"
3. **Subject line** — "Re: Planning Application [reference] — [site address]"
4. **Opening paragraph** — State {group_stylised}'s position on the application \
(based on the stance above)
5. **Body paragraphs** — Cover the key findings from the review. Each significant issue \
should be its own paragraph with inline policy citations (e.g. "contrary to paragraph 112 \
of the NPPF (December 2024)" or "as required by LTN 1/20, Section 11.1")
6. **Recommendations** — Specific, constructive improvements the group requests
7. **Suggested conditions** — Planning conditions the group requests (include this section \
when the stance is "conditional" or when the review identifies conditions)
8. **Closing paragraph** — Summarise the group's position and invite further dialogue
9. **Sign-off** — "Yours sincerely," (if named officer) or "Yours faithfully," (if \
"Dear Sir/Madam"), followed by "On behalf of {group_name} ({group_short})"
10. **References** — A bibliography listing all policy documents cited in the letter, \
with full titles, revision dates, and sections referenced

## Citation Rules
- Cite policy documents INLINE in the body text with specific paragraph/section numbers
- In the References section, list each cited document with its full title, date, and publisher
- Do not invent policy references — only cite policies mentioned in the review content

## Output Format
Output the letter as Markdown. Use heading levels for structure but keep the letter \
readable as continuous prose."""


def _build_user_prompt(
    review_result: dict[str, Any],
    stance: str,
    group_stylised: str,
    group_short: str,
    case_officer: str | None,
    letter_date: date | None,
    policy_revisions: list[dict[str, Any]] | None,
) -> str:
    """Build the user prompt with review content and metadata."""
    # Extract application metadata
    application = review_result.get("application", {}) or {}
    app_ref = application.get("reference", review_result.get("application_ref", "Unknown"))
    app_address = application.get("address", "Unknown")
    app_proposal = application.get("proposal", "Unknown")

    # Extract review content
    review = review_result.get("review", {}) or {}
    review_markdown = review.get("full_markdown", "")

    # Format letter date
    if letter_date is None:
        letter_date = date.today()
    formatted_date = letter_date.strftime("%-d %B %Y")

    # Format case officer addressing
    if case_officer:
        salutation = f"Dear {case_officer}"
        valediction = "Yours sincerely,"
    else:
        salutation = "Dear Sir/Madam"
        valediction = "Yours faithfully,"

    # Build policy references context
    policy_context = ""
    if policy_revisions:
        policy_lines = []
        for rev in policy_revisions:
            title = rev.get("title", rev.get("source_title", "Unknown"))
            rev_date = rev.get("effective_from", rev.get("date", ""))
            publisher = rev.get("publisher", "")
            line = f"- {title}"
            if rev_date:
                line += f" ({rev_date})"
            if publisher:
                line += f", {publisher}"
            policy_lines.append(line)
        policy_context = (
            "\n\n## Policy Documents Available\n"
            "The following policy documents were referenced in the review:\n"
            + "\n".join(policy_lines)
        )

    return f"""Please write a consultee response letter based on the following review.

## Letter Parameters
- **Date:** {formatted_date}
- **Salutation:** {salutation}
- **Valediction:** {valediction}
- **Stance:** {stance}
- **Group:** {group_stylised} ({group_short})

## Application Details
- **Reference:** {app_ref}
- **Address:** {app_address}
- **Proposal:** {app_proposal}

## Review Content
{review_markdown}{policy_context}

Please convert this review into a formal consultee letter following all the instructions \
in the system prompt. Remember to cite policies inline and include a references section."""
