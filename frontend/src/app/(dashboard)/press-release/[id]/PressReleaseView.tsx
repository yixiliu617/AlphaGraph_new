"use client";

/**
 * PressReleaseView -- full-page reader for an SEC EDGAR press release.
 *
 * Read-only. Replaces the older ReleaseDetailModal pop-up with a proper
 * dashboard-shell page: same top nav + sidebar via the (dashboard) route
 * group, content rendered in a constrained reading column with
 * paragraphs/headings/bullets pulled out of the raw text dump.
 */

import { useMemo } from "react";
import { ArrowLeft, ExternalLink, FileText, Loader2 } from "lucide-react";
import type { EarningsReleaseDetail } from "@/lib/api/earningsClient";

interface Props {
  release: EarningsReleaseDetail | null;
  loading: boolean;
  error:   string | null;
  onBack:  () => void;
}

export default function PressReleaseView({ release, loading, error, onBack }: Props) {
  const blocks = useMemo(
    () => (release ? paragraphize(release.text_raw) : []),
    [release],
  );

  return (
    <div className="flex flex-col h-full bg-slate-50">
      {/* Header */}
      <div className="bg-white border-b border-slate-200 shrink-0">
        <div className="max-w-4xl mx-auto px-8 py-4">
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-indigo-600 transition-colors mb-3"
          >
            <ArrowLeft size={14} /> Back to Notes
          </button>

          {loading ? (
            <div className="flex items-center gap-2 text-slate-400 text-sm py-4">
              <Loader2 size={14} className="animate-spin" /> Loading press release...
            </div>
          ) : error ? (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-4 py-2">
              {error}
            </div>
          ) : release ? (
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-start gap-3 min-w-0">
                <FileText size={18} className="text-indigo-600 shrink-0 mt-1" />
                <div className="min-w-0">
                  <h1 className="text-xl font-bold text-slate-900 leading-tight">
                    {release.title}
                  </h1>
                  <p className="text-[11px] text-slate-500 mt-1 font-mono">
                    Filed {release.filing_date}
                    {release.fiscal_period && ` | ${release.fiscal_period}`}
                    {` | ${(release.text_chars / 1024).toFixed(1)} kB of text`}
                    {release.document && ` | ${release.document}`}
                  </p>
                </div>
              </div>
              {release.url && (
                <a
                  href={release.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-[11px] font-semibold text-indigo-600 hover:text-indigo-700 border border-indigo-200 hover:border-indigo-300 bg-indigo-50 px-2.5 py-1.5 rounded transition-colors shrink-0"
                >
                  <ExternalLink size={11} /> View on EDGAR
                </a>
              )}
            </div>
          ) : null}
        </div>
      </div>

      {/* Body -- constrained reading column */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-8 py-10">
          {release && !loading && (
            <article className="prose-press">
              {blocks.map((b, i) => {
                if (b.type === "heading") {
                  return (
                    <h2 key={i} className="text-base font-bold text-slate-900 mt-8 mb-3 first:mt-0">
                      {b.text}
                    </h2>
                  );
                }
                if (b.type === "list") {
                  return (
                    <ul key={i} className="list-disc list-outside pl-6 mb-5 space-y-1.5 text-[15px] text-slate-700 leading-relaxed">
                      {b.items.map((it, j) => <li key={j}>{it}</li>)}
                    </ul>
                  );
                }
                return (
                  <p key={i} className="text-[15px] text-slate-700 leading-relaxed mb-5">
                    {b.text}
                  </p>
                );
              })}
              {blocks.length === 0 && (
                <p className="text-sm text-slate-400 italic">No body text available.</p>
              )}
            </article>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Text -> blocks (paragraph / heading / list)
// ---------------------------------------------------------------------------

type Block =
  | { type: "paragraph"; text: string }
  | { type: "heading";   text: string }
  | { type: "list";      items: string[] };

// Common section headings in EDGAR earnings press releases. Used to upgrade
// short prose-case lines to headings even when they lack the all-caps cue.
const _HEADING_RX = /^(About|Forward[- ]Looking|Conference Call|Investor|Media|Cautionary|Financial Highlights|Highlights?|Outlook|Guidance|Use of Non[- ]GAAP|Reconciliation|Webcast|Supplemental|Selected|Operational|Business|Strategic|Quarterly|Fiscal|Recent|Subsequent|Risk Factors|Safe Harbor|Q\d 20\d\d|Full Year)\b/i;

export function paragraphize(raw: string): Block[] {
  if (!raw) return [];

  const normalized = raw.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

  // Split into blocks at blank-line gaps. Also collapse runs of >=2 blanks
  // to a single boundary so multi-blank-line gaps don't create empty blocks.
  const rawBlocks = normalized
    .split(/\n[ \t]*\n+/)
    .map((b) => b.replace(/[ \t]+$/gm, "").trim())
    .filter(Boolean);

  // EDGAR text dumps sometimes have NO blank lines, just one long blob with
  // lots of single newlines. In that case the split above produces one block.
  // Fall back to splitting on lines that look like sentence boundaries:
  // a period+space+capital, or a period at end of a long line.
  let blocks = rawBlocks;
  if (rawBlocks.length === 1 && rawBlocks[0].length > 1500) {
    blocks = _splitProseFallback(rawBlocks[0]);
  }

  return blocks.map(_classifyBlock);
}

function _classifyBlock(block: string): Block {
  const lines = block.split("\n").map((l) => l.trim()).filter(Boolean);

  // Single-line short block -> probably heading.
  if (lines.length === 1) {
    const line = lines[0];
    if (_isHeadingLine(line)) {
      return { type: "heading", text: line.replace(/:$/, "").trim() };
    }
  }

  // Bullet list: every line starts with •/-/* or a number followed by . / )
  if (lines.length >= 2 && lines.every((l) => /^([•\-\*]|\d+[.)])\s+/.test(l))) {
    return {
      type: "list",
      items: lines.map((l) => l.replace(/^([•\-\*]|\d+[.)])\s+/, "").trim()),
    };
  }

  // Paragraph: collapse internal newlines into spaces so word-wrap is natural.
  return { type: "paragraph", text: lines.join(" ") };
}

function _isHeadingLine(line: string): boolean {
  if (line.length > 100) return false;
  if (line.length < 3) return false;
  // ALL CAPS (allowing digits, basic punctuation, spaces)
  if (/^[A-Z][A-Z0-9 .,&\-():'#$%/]+$/.test(line)) return true;
  // Ends with `:` and looks like a label
  if (line.endsWith(":") && line.length < 80 && !line.includes(". ")) return true;
  // Matches known section keyword at start
  if (_HEADING_RX.test(line)) return true;
  return false;
}

// Fallback for EDGAR dumps that come as one big blob with no blank lines.
// Look for the conventional press-release section markers and split there.
function _splitProseFallback(text: string): string[] {
  // Insert a paragraph break before lines that look like new paragraphs:
  // a sentence ending with `.` followed by a capital letter that opens a new
  // line. We do this on already-newline-separated input first.
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);

  // If even single-newline lines aren't useful, fall back to a single paragraph.
  if (lines.length <= 1) return [text];

  // Group consecutive lines until we see a likely section break -- a heading
  // line, a bullet, or a long prose end-of-paragraph (line ending with `.`
  // followed by a heading-looking next line).
  const groups: string[] = [];
  let buf: string[] = [];

  const flush = () => {
    if (buf.length > 0) {
      groups.push(buf.join("\n"));
      buf = [];
    }
  };

  for (const line of lines) {
    if (_isHeadingLine(line)) {
      flush();
      groups.push(line);
    } else if (/^([•\-\*]|\d+[.)])\s+/.test(line)) {
      // Bullet -- belongs with adjacent bullets
      buf.push(line);
      // Don't flush yet; classifier will detect the list shape.
    } else {
      buf.push(line);
    }
  }
  flush();

  return groups;
}
