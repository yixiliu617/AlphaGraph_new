import { Activity, Bell, TrendingUp, AlertTriangle } from "lucide-react";

export default function MonitorsPage() {
  return (
    <div className="flex flex-col h-full space-y-4 p-6 overflow-y-auto">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold tracking-tight">Real-Time Monitors</h2>
        <div className="flex items-center gap-2">
          <button className="p-2 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors relative">
            <Bell size={16} />
            <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-red-500 rounded-full border-2 border-white" />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-4">
        {/* Catalyst Feed */}
        <div className="col-span-8 space-y-4">
          <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-6">
            <h3 className="text-sm font-semibold mb-6 flex items-center gap-2">
              <AlertTriangle size={16} className="text-amber-500" />
              Active Catalyst Pings
            </h3>
            <div className="space-y-4 py-10 text-center">
              <p className="text-xs text-slate-400 font-mono italic">Ambient agents are monitoring your Thesis Ledger.</p>
            </div>
          </div>
        </div>

        {/* Fundamental Monitors */}
        <div className="col-span-4 space-y-4">
          <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-6">
            <h3 className="text-sm font-semibold mb-6 flex items-center gap-2">
              <TrendingUp size={16} className="text-blue-500" />
              Fundamental Alerts
            </h3>
            <div className="space-y-4">
              <p className="text-[10px] text-slate-400 uppercase tracking-widest font-bold">Watchlist Metrics</p>
              <div className="space-y-2 py-4 text-center">
                <p className="text-[10px] text-slate-400 font-mono italic">No metrics currently triggering alerts.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
