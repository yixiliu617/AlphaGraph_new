"use client";

import { FileText, Download, MoreHorizontal } from "lucide-react";

interface TextBlockProps {
  title: string;
  content: string;
}

export default function TextBlock({ title, content }: TextBlockProps) {
  return (
    <div className="flex flex-col h-full w-full bg-white p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="p-1.5 bg-slate-100 rounded text-slate-500">
            <FileText size={14} />
          </div>
          <h4 className="text-xs font-bold uppercase tracking-widest text-slate-900">{title}</h4>
        </div>
        <div className="flex items-center gap-2">
          <button className="p-1 text-slate-400 hover:text-slate-900 transition-colors">
            <Download size={14} />
          </button>
          <button className="p-1 text-slate-400 hover:text-slate-900 transition-colors">
            <MoreHorizontal size={14} />
          </button>
        </div>
      </div>
      
      <div className="flex-1 overflow-y-auto pr-2">
        <p className="text-sm leading-relaxed text-slate-700 whitespace-pre-wrap">
          {content}
        </p>
      </div>
      
      <div className="pt-2 border-t border-slate-50">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono bg-slate-100 px-1.5 py-0.5 rounded text-slate-500">
            Source Verified
          </span>
          <span className="text-[10px] text-slate-400 font-mono">
            Lineage: L1_Fragment
          </span>
        </div>
      </div>
    </div>
  );
}
