import { PenTool, Save, Send } from "lucide-react";

export default function SynthesisPage() {
  return (
    <div className="flex flex-col h-full space-y-4 p-6 overflow-hidden">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold tracking-tight">Thesis Synthesis</h2>
        <div className="flex items-center gap-2">
          <button className="px-3 py-1.5 text-xs font-medium bg-white border border-slate-200 rounded hover:bg-slate-50 flex items-center gap-2">
            <Save size={14} /> Save Draft
          </button>
          <button className="px-3 py-1.5 text-xs font-medium bg-slate-900 text-white rounded hover:bg-slate-800 transition-colors flex items-center gap-2">
            <Send size={14} /> Update Ledger
          </button>
        </div>
      </div>

      <div className="flex-1 flex gap-4 overflow-hidden">
        {/* Editor Area */}
        <div className="flex-1 bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden flex flex-col p-8 space-y-6 overflow-y-auto">
          <div className="max-w-3xl mx-auto w-full space-y-6">
            <input 
              className="text-4xl font-bold bg-transparent border-none outline-none placeholder:text-slate-200"
              placeholder="Thesis Title"
            />
            <div className="flex items-center gap-2 p-1.5 bg-slate-50 border border-slate-100 rounded-lg w-fit">
              <span className="text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 bg-slate-900 text-white rounded">Long</span>
              <span className="text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 text-slate-500">AAPL</span>
            </div>
            <textarea 
              className="w-full flex-1 bg-transparent border-none outline-none resize-none text-slate-700 leading-relaxed min-h-[400px]"
              placeholder="Start building your research synthesis by dragging fragments from the engine..."
            />
          </div>
        </div>

        {/* Fragment Panel */}
        <div className="w-80 flex flex-col gap-4">
          <div className="p-4 bg-white border border-slate-200 rounded-xl flex-1 shadow-sm overflow-hidden flex flex-col">
            <h4 className="text-sm font-semibold mb-3">Collected Fragments</h4>
            <div className="flex-1 overflow-y-auto space-y-2 p-1">
              <p className="text-[10px] text-slate-400 font-mono italic">No fragments collected for this thesis.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
