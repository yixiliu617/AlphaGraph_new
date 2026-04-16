export default function TopologyPage() {
  return (
    <div className="flex flex-col h-full space-y-4 p-6 overflow-y-auto">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold tracking-tight">Topology Map</h2>
        <div className="flex items-center gap-2">
          <button className="px-3 py-1.5 text-xs font-medium bg-white border border-slate-200 rounded hover:bg-slate-50">
            Export JSON
          </button>
        </div>
      </div>
      <div className="flex-1 bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm relative">
        <div className="absolute inset-0 flex items-center justify-center text-slate-400">
          <p className="text-sm font-mono">[Neo4j Graph Visualization Container]</p>
        </div>
      </div>
    </div>
  );
}
