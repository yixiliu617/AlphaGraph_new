"use client";

import { mapAgentBlockToProps } from "@/lib/mappers/mapAgentBlock";
import MetricChart from "@/components/domain/charts/MetricChart";
import TextBlock from "@/components/domain/blocks/TextBlock";
import FinancialTableBlock from "@/components/domain/blocks/FinancialTableBlock";
import type { FinancialTableData } from "@/components/domain/blocks/FinancialTableBlock";

interface AgentBlockRendererProps {
  block: any;
}

export default function AgentBlockRenderer({ block }: AgentBlockRendererProps) {
  const props = mapAgentBlockToProps(block);

  return (
    <div className="group relative border border-slate-200 rounded-xl overflow-hidden bg-white shadow-sm hover:shadow-md transition-shadow">
      {/* Draggable Handle Overlay (Visible on Hover) */}
      <div className="absolute top-0 left-0 w-full h-1.5 bg-slate-900 opacity-0 group-hover:opacity-100 transition-opacity cursor-grab active:cursor-grabbing" />

      {block.block_type === "chart" && (
        <MetricChart {...(props as any)} />
      )}

      {block.block_type === "text" && (
        <TextBlock {...(props as any)} />
      )}

      {block.block_type === "financial_table" && (
        <FinancialTableBlock
          title={block.title}
          data={block.data as FinancialTableData}
        />
      )}

      {/* Fallback for unsupported block types */}
      {!["chart", "text", "financial_table"].includes(block.block_type) && (
        <div className="p-6 text-center text-slate-400 font-mono text-xs">
          [Unsupported Block: {block.block_type}]
        </div>
      )}
    </div>
  );
}
