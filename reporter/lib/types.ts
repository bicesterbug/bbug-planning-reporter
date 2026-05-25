// Shared types for the reporter serverless control plane.

export type ReviewMode = "autonomous" | "cowork";

// Custom tools the Managed Agent may call. Each is serviced host-side by the
// webhook driver (nothing is exposed to the agent container), and each returns
// COMPACT JSON only — the token-vs-self-host firewall.
export type CustomToolName =
  | "fetch_application"
  | "download_docs"
  | "ingest_document"
  | "search_application_docs"
  | "search_policy"
  | "get_policy_section"
  | "assess_cycle_route"
  | "get_site_boundary"
  | "export_response"
  | "classify_documents"
  | "generate_search_queries"
  | "verify_claims"
  | "submit_mitigation_inference"; // high-risk: gated for human review

// A pending tool call surfaced on the session event stream.
export interface PendingToolCall {
  eventId: string; // sevt_... — used as custom_tool_use_id in the result
  name: string;
  input: Record<string, unknown>;
}

export interface ToolResult {
  content: string; // compact JSON string
  isError: boolean;
  escalate?: boolean; // hard-escalation flag surfaced by deterministic checks
  escalateReason?: string;
}
