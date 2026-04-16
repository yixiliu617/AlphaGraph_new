// ---------------------------------------------------------------------------
// AgentBlock mapper — the seam between backend responses and dumb components.
//
// Runtime type guards here mean any backend schema change surfaces immediately
// as a console error, not as a silent broken UI somewhere deep in a component.
// ---------------------------------------------------------------------------

type KnownBlockType = "chart" | "text";

interface RawAgentBlock {
  block_type: string;
  title: string;
  data: unknown;
}

/**
 * Assert that an unknown value has the minimum shape of an AgentResponseBlock.
 * Throws a descriptive error so the dev sees exactly which field is wrong.
 */
function assertIsRawBlock(raw: unknown): asserts raw is RawAgentBlock {
  if (!raw || typeof raw !== "object") {
    throw new TypeError(`AgentBlock must be an object — received: ${typeof raw}`);
  }
  const b = raw as Record<string, unknown>;
  if (typeof b.block_type !== "string") {
    throw new TypeError(
      `AgentBlock.block_type must be a string — received: ${typeof b.block_type}`
    );
  }
  if (typeof b.title !== "string") {
    throw new TypeError(
      `AgentBlock.title must be a string — received: ${typeof b.title}`
    );
  }
}

export function mapFragmentToChartData(
  rawMetrics: unknown
): { name: string; value: number }[] {
  if (!rawMetrics || typeof rawMetrics !== "object") return [];

  return Object.entries(rawMetrics as Record<string, unknown>).map(([key, value]) => ({
    name: key.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase()),
    value: typeof value === "number" ? value : 0,
  }));
}

export type MappedChartProps = { title: string; data: { name: string; value: number }[]; type: "bar" };
export type MappedTextProps  = { title: string; content: string };
export type MappedFallback   = { title: string; data: unknown };

export type MappedBlockProps = MappedChartProps | MappedTextProps | MappedFallback;

/**
 * Translate a raw AgentResponseBlock from the backend into typed props for
 * dumb presentational components.
 *
 * If the block fails validation (e.g. backend changed its schema), an error
 * is logged to the console and a safe fallback is returned so the UI doesn't
 * crash.
 */
export function mapAgentBlockToProps(raw: unknown): MappedBlockProps {
  try {
    assertIsRawBlock(raw);

    switch (raw.block_type as KnownBlockType) {
      case "chart":
        return {
          title: raw.title,
          data:  mapFragmentToChartData(raw.data),
          type:  "bar",
        } satisfies MappedChartProps;

      case "text":
        return {
          title:   raw.title,
          content: typeof raw.data === "string"
            ? raw.data
            : JSON.stringify(raw.data, null, 2),
        } satisfies MappedTextProps;

      default:
        return { title: raw.title, data: raw.data };
    }
  } catch (err) {
    console.error("[mapAgentBlockToProps] Block validation failed:", err, raw);
    return { title: "Invalid Block", data: null };
  }
}
