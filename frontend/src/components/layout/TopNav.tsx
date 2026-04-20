"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Network, Terminal, Library, PenTool, Activity, NotebookPen, BarChart2, MessageSquare,
} from "lucide-react";

const TABS = [
  { name: "Topology",      href: "/topology",       icon: Network },
  { name: "Engine",        href: "/engine",          icon: Terminal },
  { name: "Library",       href: "/library",         icon: Library },
  { name: "Notes",         href: "/notes",           icon: NotebookPen },
  { name: "DataExplorer",  href: "/data-explorer",   icon: BarChart2 },
  { name: "Social Media",  href: "/social-media",    icon: MessageSquare },
  { name: "Synthesis",     href: "/synthesis",       icon: PenTool },
  { name: "Monitors",      href: "/monitors",        icon: Activity },
];

export default function TopNav() {
  const pathname = usePathname();

  return (
    <header className="h-14 shrink-0 bg-white border-b border-slate-200 flex items-center px-5 gap-5 z-30 shadow-sm">
      {/* Logo */}
      <Link href="/notes" className="flex items-center gap-2.5 shrink-0 select-none">
        <div className="h-7 w-7 rounded-lg bg-indigo-600 flex items-center justify-center text-white text-xs font-bold tracking-tight">
          AG
        </div>
        <div className="leading-none">
          <span className="block text-sm font-bold text-slate-900">AlphaGraph</span>
          <span className="block text-[10px] text-slate-400 font-mono">Institutional Research</span>
        </div>
      </Link>

      {/* Divider */}
      <div className="w-px h-6 bg-slate-200 shrink-0" />

      {/* Tab pills */}
      <nav className="flex items-center gap-0.5 flex-1 overflow-x-auto no-scrollbar">
        {TABS.map((tab) => {
          const isActive =
            pathname === tab.href ||
            (tab.href !== "/" && pathname.startsWith(tab.href));
          return (
            <Link
              key={tab.name}
              href={tab.href}
              className={[
                "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium whitespace-nowrap transition-colors",
                isActive
                  ? "bg-slate-900 text-white"
                  : "text-slate-500 hover:text-slate-900 hover:bg-slate-100",
              ].join(" ")}
            >
              <tab.icon size={14} className={isActive ? "opacity-90" : "opacity-60"} />
              {tab.name}
            </Link>
          );
        })}
      </nav>

      {/* Right side */}
      <div className="flex items-center gap-3 shrink-0 ml-auto">
        {/* Tenant indicator */}
        <div className="hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-indigo-50 border border-indigo-100">
          <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse shrink-0" />
          <span className="text-[11px] font-medium text-indigo-700">Institutional_L1</span>
        </div>

        {/* User avatar */}
        <div className="w-7 h-7 rounded-full bg-slate-200 flex items-center justify-center text-[11px] font-bold text-slate-600 select-none">
          PM
        </div>
      </div>
    </header>
  );
}
