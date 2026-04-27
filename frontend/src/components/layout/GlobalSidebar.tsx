"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Network,
  Terminal,
  Library,
  PenTool,
  Activity,
  NotebookPen,
  Settings,
  MessageSquare,
  Flag,
  CalendarDays,
} from "lucide-react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const TABS = [
  { name: "Topology", href: "/topology", icon: Network },
  { name: "Unified Engine", href: "/engine", icon: Terminal },
  { name: "Library", href: "/library", icon: Library },
  { name: "Notes", href: "/notes", icon: NotebookPen },
  { name: "Social Media", href: "/social-media", icon: MessageSquare },
  { name: "Taiwan", href: "/taiwan", icon: Flag },
  { name: "Synthesis", href: "/synthesis", icon: PenTool },
  { name: "Calendar", href: "/calendar", icon: CalendarDays },
  { name: "Monitors", href: "/monitors", icon: Activity },
];

export default function GlobalSidebar() {
  const pathname = usePathname();

  return (
    <div className="w-64 h-full border-r border-slate-200 bg-white flex flex-col shrink-0 shadow-sm">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-slate-100">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white text-xs font-bold shrink-0">
          AG
        </div>
        <div>
          <h1 className="text-sm font-bold tracking-tight text-slate-900">AlphaGraph</h1>
          <p className="text-[10px] text-slate-400 font-mono leading-none mt-0.5">Institutional Research</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 p-3 space-y-0.5 overflow-y-auto">
        {TABS.map((tab) => {
          const isActive = pathname.startsWith(tab.href);
          return (
            <Link
              key={tab.name}
              href={tab.href}
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors",
                isActive
                  ? "bg-indigo-50 text-indigo-700"
                  : "text-slate-500 hover:bg-slate-50 hover:text-slate-900"
              )}
            >
              <tab.icon
                size={17}
                className={cn(isActive ? "text-indigo-600" : "text-slate-400")}
              />
              {tab.name}
              {isActive && (
                <span className="ml-auto w-1.5 h-1.5 rounded-full bg-indigo-500" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="p-3 border-t border-slate-100 space-y-0.5">
        <Link
          href="/settings"
          className={cn(
            "flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors",
            pathname.startsWith("/settings")
              ? "bg-indigo-50 text-indigo-700"
              : "text-slate-500 hover:bg-slate-50 hover:text-slate-900"
          )}
        >
          <Settings size={17} className={cn(pathname.startsWith("/settings") ? "text-indigo-600" : "text-slate-400")} />
          Universe
        </Link>

        <div className="flex items-center gap-3 px-3 py-2 rounded-lg bg-slate-50 mt-1">
          <div className="w-7 h-7 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-700 text-xs font-bold shrink-0">
            PM
          </div>
          <div className="overflow-hidden">
            <p className="text-xs font-semibold truncate text-slate-800">Lead PM / Alpha Capital</p>
            <p className="text-[10px] text-slate-400 truncate">Institutional_L1</p>
          </div>
        </div>
      </div>
    </div>
  );
}
