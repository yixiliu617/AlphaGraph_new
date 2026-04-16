"use client";

import { useState, useRef, useEffect } from "react";
import { X, Plus } from "lucide-react";
import { useUniverseStore } from "@/store/useUniverseStore";

const NOTE_TYPES = [
  { value: "earnings_call",      label: "Earnings Call" },
  { value: "management_meeting", label: "Management Meeting" },
  { value: "conference",         label: "Conference / NDR" },
  { value: "internal",           label: "Internal Note" },
];

interface Props {
  onClose: () => void;
  onCreate: (payload: {
    title: string;
    note_type: string;
    company_tickers: string[];
    meeting_date?: string;
  }) => void;
}

export default function NoteCreationModal({ onClose, onCreate }: Props) {
  const { tickers: universeTickers } = useUniverseStore();

  const [savedCustomTypes, setSavedCustomTypes] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem("ag_custom_note_types") ?? "[]");
    } catch { return []; }
  });

  const [title, setTitle]               = useState("");
  const [noteType, setNoteType]         = useState("");
  const [customType, setCustomType]     = useState("");
  const [showCustomType, setShowCustom] = useState(false);
  const [companies, setCompanies]       = useState<string[]>([]);
  const [companyInput, setCompanyInput] = useState("");
  const [suggestions, setSuggestions]   = useState<{ symbol: string; name: string }[]>([]);
  const [meetingDate, setMeetingDate]   = useState(() => new Date().toISOString().slice(0, 10));
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errors, setErrors]             = useState<Record<string, string>>({});

  const inputRef    = useRef<HTMLInputElement>(null);
  const overlayRef  = useRef<HTMLDivElement>(null);
  const companyRef  = useRef<HTMLInputElement>(null);

  useEffect(() => { setTimeout(() => inputRef.current?.focus(), 50); }, []);

  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === overlayRef.current) onClose();
  };

  // ── Company input ──────────────────────────────────────────────────────────

  const handleCompanyInput = (value: string) => {
    setCompanyInput(value);
    const q = value.trim().toLowerCase();
    if (!q) { setSuggestions([]); return; }
    const matches = universeTickers
      .filter(
        (t) =>
          (t.symbol.toLowerCase().includes(q) || t.name.toLowerCase().includes(q)) &&
          !companies.includes(t.symbol)
      )
      .slice(0, 6);
    setSuggestions(matches);
  };

  const addCompany = (value: string) => {
    const v = value.trim();
    if (!v || companies.includes(v)) return;
    setCompanies((prev) => [...prev, v]);
    setCompanyInput("");
    setSuggestions([]);
    companyRef.current?.focus();
  };

  const removeCompany = (c: string) => {
    setCompanies((prev) => prev.filter((x) => x !== c));
  };

  const handleCompanyKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      if (companyInput.trim()) addCompany(companyInput);
    }
    if (e.key === "Backspace" && !companyInput && companies.length > 0) {
      removeCompany(companies[companies.length - 1]);
    }
    if (e.key === "Escape") setSuggestions([]);
  };

  // ── Note type ──────────────────────────────────────────────────────────────

  const selectPreset = (value: string) => {
    setNoteType(value);
    setShowCustom(false);
    setCustomType("");
  };

  const selectCustom = () => {
    setNoteType("__custom__");
    setShowCustom(true);
  };

  const effectiveType = noteType === "__custom__" ? customType.trim() : noteType;

  // ── Validation & submit ────────────────────────────────────────────────────

  const validate = () => {
    const errs: Record<string, string> = {};
    if (!title.trim()) errs.title = "Title is required.";
    if (!effectiveType) errs.noteType = noteType === "__custom__"
      ? "Enter a custom meeting type."
      : "Please select a note type.";
    if (companies.length === 0) errs.companies = "Add at least one company.";
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const handleSubmit = async () => {
    if (!validate() || isSubmitting) return;
    setIsSubmitting(true);

    // Persist custom type for future suggestions
    if (noteType === "__custom__" && customType.trim() && !savedCustomTypes.includes(customType.trim())) {
      const updated = [...savedCustomTypes, customType.trim()];
      setSavedCustomTypes(updated);
      localStorage.setItem("ag_custom_note_types", JSON.stringify(updated));
    }

    await onCreate({
      title: title.trim(),
      note_type: effectiveType,
      company_tickers: companies,
      meeting_date: meetingDate || undefined,
    });
    setIsSubmitting(false);
  };

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden border border-slate-200">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 bg-slate-50">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-md bg-indigo-600 flex items-center justify-center">
              <Plus size={14} className="text-white" />
            </div>
            <h3 className="text-sm font-semibold text-slate-900">New Note</h3>
          </div>
          <button onClick={onClose} className="p-1 text-slate-400 hover:text-slate-600 rounded-lg transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Form */}
        <div className="px-6 py-5 space-y-5">

          {/* Title */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1.5 uppercase tracking-wider">
              Title <span className="text-red-400">*</span>
            </label>
            <input
              ref={inputRef}
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
              placeholder="NVDA Q1 FY26 Earnings Call"
              className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md bg-slate-50 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 text-slate-900 placeholder-slate-400"
            />
            {errors.title && <p className="mt-1 text-xs text-red-500">{errors.title}</p>}
          </div>

          {/* Note type */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1.5 uppercase tracking-wider">
              Meeting Type <span className="text-red-400">*</span>
            </label>
            <div className="grid grid-cols-2 gap-2">
              {NOTE_TYPES.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  onClick={() => selectPreset(t.value)}
                  className={`px-3 py-2.5 text-xs font-medium rounded-md border text-left transition-colors ${
                    noteType === t.value
                      ? "border-indigo-600 bg-indigo-600 text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300 hover:text-indigo-600"
                  }`}
                >
                  {t.label}
                </button>
              ))}
              {savedCustomTypes.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => selectPreset(t)}
                  className={`px-3 py-2.5 text-xs font-medium rounded-md border text-left transition-colors ${
                    noteType === t
                      ? "border-indigo-600 bg-indigo-600 text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300 hover:text-indigo-600"
                  }`}
                >
                  {t}
                </button>
              ))}
              <button
                type="button"
                onClick={selectCustom}
                className={`px-3 py-2.5 text-xs font-medium rounded-md border text-left transition-colors col-span-2 ${
                  noteType === "__custom__"
                    ? "border-indigo-600 bg-indigo-50 text-indigo-700"
                    : "border-dashed border-slate-300 bg-white text-slate-500 hover:border-indigo-300 hover:text-indigo-600"
                }`}
              >
                Other / Custom…
              </button>
            </div>
            {showCustomType && (
              <input
                autoFocus
                type="text"
                value={customType}
                onChange={(e) => setCustomType(e.target.value)}
                placeholder="e.g. Site Visit, Channel Check, Roadshow…"
                className="mt-2 w-full px-3 py-2 text-sm border border-indigo-300 rounded-md bg-indigo-50 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 text-slate-900 placeholder-slate-400"
              />
            )}
            {errors.noteType && <p className="mt-1 text-xs text-red-500">{errors.noteType}</p>}
          </div>

          {/* Companies */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1.5 uppercase tracking-wider">
              Companies <span className="text-red-400">*</span>
            </label>
            <div className="flex flex-wrap gap-1.5 p-2.5 border border-slate-200 rounded-md min-h-[42px] focus-within:ring-1 focus-within:ring-indigo-500 focus-within:border-indigo-500 bg-slate-50 transition-shadow">
              {companies.map((c) => (
                <span
                  key={c}
                  className="flex items-center gap-1 px-2 py-0.5 text-xs font-semibold bg-indigo-600 text-white rounded"
                >
                  {c}
                  <button onClick={() => removeCompany(c)} className="hover:text-indigo-200 transition-colors">
                    <X size={10} />
                  </button>
                </span>
              ))}
              <div className="relative flex-1 min-w-[140px]">
                <input
                  ref={companyRef}
                  type="text"
                  value={companyInput}
                  onChange={(e) => handleCompanyInput(e.target.value)}
                  onKeyDown={handleCompanyKeyDown}
                  placeholder={companies.length === 0 ? "NVDA, Tencent, 9999.HK…" : "Add more…"}
                  className="w-full text-sm outline-none bg-transparent placeholder-slate-400 text-slate-800"
                />
                {suggestions.length > 0 && (
                  <div className="absolute top-full left-0 mt-1 w-64 bg-white border border-slate-200 rounded-lg shadow-lg z-10 overflow-hidden">
                    {suggestions.map((s) => (
                      <button
                        key={s.symbol}
                        onMouseDown={(e) => { e.preventDefault(); addCompany(s.symbol); }}
                        className="w-full text-left px-3 py-2 text-xs hover:bg-indigo-50 hover:text-indigo-700 flex items-center gap-2 transition-colors"
                      >
                        <span className="font-mono font-semibold shrink-0">{s.symbol}</span>
                        <span className="text-slate-400 truncate">{s.name}</span>
                      </button>
                    ))}
                    {companyInput.trim() && !suggestions.find((s) => s.symbol === companyInput.trim()) && (
                      <button
                        onMouseDown={(e) => { e.preventDefault(); addCompany(companyInput.trim()); }}
                        className="w-full text-left px-3 py-2 text-xs hover:bg-slate-50 flex items-center gap-2 border-t border-slate-100 text-slate-500 transition-colors"
                      >
                        <Plus size={11} className="shrink-0" />
                        Add &quot;{companyInput.trim()}&quot; as-is
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
            <p className="mt-1 text-[10px] text-slate-400">
              Type ticker or name + Enter. Accepts any format: NVDA, Tencent, 9999.HK, Zhipu…
            </p>
            {errors.companies && <p className="mt-1 text-xs text-red-500">{errors.companies}</p>}
          </div>

          {/* Meeting date */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1.5 uppercase tracking-wider">
              Meeting Date <span className="text-slate-400 font-normal normal-case">(optional)</span>
            </label>
            <input
              type="date"
              value={meetingDate}
              onChange={(e) => setMeetingDate(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md bg-slate-50 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 text-slate-700"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-slate-200 bg-slate-50">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={isSubmitting}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50 transition-colors shadow-sm"
          >
            <Plus size={14} />
            {isSubmitting ? "Creating…" : "Create Note"}
          </button>
        </div>
      </div>
    </div>
  );
}
