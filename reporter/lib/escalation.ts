// Confidence gates + hard-escalation rules (autonomous mode).
//
// The headline recommendation (support/object/conditional) is NEVER decided by
// the agent — that is always handed to a human. These rules decide when an
// otherwise-autonomous run must additionally pause for human review.

// Per-stage confidence thresholds. Mitigation inference is highest-risk because
// there is no tool to validate it (the OSM baseline-only constraint).
export const CONFIDENCE_THRESHOLDS: Record<string, number> = {
  triage_classification: 0.85,
  gap_detection: 0.8,
  baseline_route: 0.75,
  mitigation_inference: 0.9,
  policy_compliance: 0.8,
  asks_drafting: 0.85,
};

export function belowThreshold(stage: string, confidence: number): boolean {
  const t = CONFIDENCE_THRESHOLDS[stage];
  return t !== undefined && confidence < t;
}

// Hard-escalation rules — always escalate regardless of model confidence.
// These are evaluated against the compact JSON returned by the deterministic
// tools, so they are cheap and trustworthy.
export interface EscalationSignal {
  rule: string;
  detail: string;
}

export function hardEscalations(facts: {
  applicationType?: string;
  dateValidated?: string | null;
  taDateIso?: string | null; // date of the submitted Transport Assessment, if known
  withinAQMA?: boolean;
  lcwipAdjacent?: boolean;
  ocrFailedOrRedacted?: boolean;
  quantifiedS106orS278?: boolean;
  priorRelatedResponse?: boolean;
  credibilityConcerns?: string[];
  routeAssessments?: Array<{ rating: string; toSchoolCatchment?: boolean }>;
}): EscalationSignal[] {
  const signals: EscalationSignal[] = [];
  const now = Date.now();

  if (facts.taDateIso) {
    const ageMonths = (now - Date.parse(facts.taDateIso)) / (1000 * 60 * 60 * 24 * 30.44);
    if (ageMonths > 18) {
      signals.push({ rule: "stale_transport_assessment", detail: `TA/TS ~${Math.round(ageMonths)} months old` });
    }
  }
  if (facts.withinAQMA) signals.push({ rule: "within_aqma", detail: "Application within an Air Quality Management Area" });
  if (facts.lcwipAdjacent)
    signals.push({ rule: "lcwip_adjacent", detail: "On/adjacent to an LCWIP route — high strategic significance" });
  if (facts.ocrFailedOrRedacted)
    signals.push({ rule: "ocr_failed_or_redacted", detail: "A document failed OCR or appears redacted" });
  if (facts.quantifiedS106orS278)
    signals.push({ rule: "quantified_obligations", detail: "Proposed S106/S278 with quantified contributions" });
  if (facts.priorRelatedResponse)
    signals.push({ rule: "precedent_risk", detail: "Group has previously responded to a related application" });
  for (const c of facts.credibilityConcerns ?? [])
    signals.push({ rule: "mitigation_credibility_concern", detail: c });
  for (const r of facts.routeAssessments ?? [])
    if (r.rating === "red" && r.toSchoolCatchment)
      signals.push({ rule: "lts4_to_school", detail: "Red-rated (LTS-4) route to a school catchment" });

  return signals;
}
