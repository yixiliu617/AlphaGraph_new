import { FileText, MoreHorizontal, Download, ChevronRight } from "lucide-react";

export default function LibraryPage() {
  return (
    <div className="flex flex-col h-full space-y-4 p-6 overflow-y-auto">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold tracking-tight">Research Library</h2>
        <div className="flex items-center gap-2">
          <button className="px-3 py-1.5 text-xs font-medium bg-slate-900 text-white rounded hover:bg-slate-800 transition-colors">
            Upload Report
          </button>
        </div>
      </div>

      <div className="flex-1 bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden flex flex-col">
        {/* Table Header */}
        <div className="grid grid-cols-12 gap-4 p-4 border-b border-slate-100 bg-slate-50 text-[10px] font-bold uppercase tracking-wider text-slate-500">
          <div className="col-span-6 flex items-center gap-2">Document Name</div>
          <div className="col-span-2">Type</div>
          <div className="col-span-2">Extracted Metrics</div>
          <div className="col-span-2 text-right">Date Added</div>
        </div>

        {/* Table Body (Empty State) */}
        <div className="flex-1 p-6 text-center space-y-4 pt-20">
          <div className="flex flex-col items-center gap-2">
            <div className="w-12 h-12 bg-slate-100 rounded-full flex items-center justify-center text-slate-400">
              <FileText size={20} />
            </div>
            <h3 className="text-sm font-medium">Your research inventory is empty.</h3>
            <p className="text-xs text-slate-500">Import broker reports, transcripts, or notes to start extracting Data_Fragments.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
