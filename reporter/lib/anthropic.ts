// Anthropic client + Managed Agents helpers.
//
// Mandatory flow: Agent (created once, ID stored in env) → Session (every run).
// The agent config (model/system/skills/tools) lives in agent/agent.yaml and is
// applied with the `ant` CLI; this module only references the stored AGENT_ID +
// ENVIRONMENT_ID and drives sessions.

import Anthropic from "@anthropic-ai/sdk";

export const AGENT_LOOP_MODEL = "claude-opus-4-7"; // reasoning loop
export const CLASSIFIER_MODEL = "claude-haiku-4-5"; // cheap host-side classification

export function getClient(): Anthropic {
  return new Anthropic({ apiKey: requireEnv("ANTHROPIC_API_KEY") });
}

export function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`Missing required env var: ${name}`);
  return v;
}

export function agentRef(): { agentId: string; environmentId: string } {
  return {
    agentId: requireEnv("REPORTER_AGENT_ID"),
    environmentId: requireEnv("REPORTER_ENVIRONMENT_ID"),
  };
}

// Idle stop_reason that means "the agent is waiting on us" (tool confirmation or
// custom tool result) rather than "done". See managed-agents client patterns.
export function isTerminalIdle(stopReasonType: string | undefined): boolean {
  return stopReasonType !== undefined && stopReasonType !== "requires_action";
}
