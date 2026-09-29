import { createSignal } from "solid-js";

export type EvidenceTarget =
  | { type: "handle"; handle: string; title?: string }
  | { type: "canonical"; kind: string; id: number }
  | { type: "text_unit"; id: number }
  | { type: "conversation"; id: number }
  | { type: "claim"; id: number };

// The drawer keeps its own back-stack so drilling evidence -> record -> thread never loses place.
const [evidenceStack, setEvidenceStack] = createSignal<EvidenceTarget[]>([]);
export { evidenceStack };
export const openEvidence = (t: EvidenceTarget, replace = false) =>
  setEvidenceStack((s) => (replace ? [t] : [...s, t]));
export const openEvidenceFresh = (t: EvidenceTarget) => setEvidenceStack([t]);
export const evidenceBack = () => setEvidenceStack((s) => s.slice(0, -1));
export const closeEvidence = () => setEvidenceStack([]);

// Workspace context carried into follow-up questions (current relationship + window).
export type AskContext = { relationship_key?: string; label?: string; from?: string; to?: string };
const [askContext, setAskContext] = createSignal<AskContext>({});
export { askContext, setAskContext };

const [includeText, setIncludeText] = createSignal(false);
export { includeText, setIncludeText };

export const fmtNum = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString());
export const fmtDate = (d: string | null | undefined) => (d ? d.slice(0, 10) : "—");
export const fmtMonth = (d: string) => {
  const [y, m] = d.split("-");
  return `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+m - 1]} ${y}`;
};
