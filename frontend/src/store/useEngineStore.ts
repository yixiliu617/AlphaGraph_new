// ---------------------------------------------------------------------------
// Re-export shim — the canonical store now lives next to its feature.
// New code should import from "@/app/(dashboard)/engine/store" directly.
// ---------------------------------------------------------------------------

export { useEngineStore, type AgentBlock, type Message } from "@/app/(dashboard)/engine/store";
