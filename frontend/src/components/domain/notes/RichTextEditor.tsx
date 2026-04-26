"use client";

/**
 * RichTextEditor — Notion-like editor built on Tiptap.
 *
 * Keyboard shortcuts (same as Notion):
 *   Ctrl/Cmd + B         → Bold
 *   Ctrl/Cmd + I         → Italic
 *   Ctrl/Cmd + U         → Underline (via StarterKit keyboard map)
 *   Ctrl/Cmd + Shift + S → Strikethrough
 *   Ctrl/Cmd + E         → Inline code
 *   Ctrl/Cmd + Shift + 7 → Ordered list
 *   Ctrl/Cmd + Shift + 8 → Bullet list
 *   Ctrl/Cmd + Shift + 9 → Blockquote
 *   #  (space)           → Heading 1
 *   ## (space)           → Heading 2
 *   ### (space)          → Heading 3
 *   - or * (space)       → Bullet list
 *   1. (space)           → Ordered list
 *   > (space)            → Blockquote
 *   --- (Enter)          → Horizontal rule
 *   /                    → Slash command menu
 *   Tab                  → Increase list indent
 *   Shift+Tab            → Decrease list indent
 *
 * Slash commands (type / anywhere):
 *   /h1, /h2, /h3, /bullet, /numbered, /table, /image, /divider, /quote
 */

import { useEditor, EditorContent, BubbleMenu, Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import Table from "@tiptap/extension-table";
import TableRow from "@tiptap/extension-table-row";
import TableCell from "@tiptap/extension-table-cell";
import TableHeader from "@tiptap/extension-table-header";
import Image from "@tiptap/extension-image";
import Typography from "@tiptap/extension-typography";
import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import {
  Bold, Italic, Strikethrough, Code, List, ListOrdered, Quote
} from "lucide-react";
import { useCallback, useState, useRef, useEffect } from "react";
import { SectionHeading } from "./sectionHeadingExtension";

// ---------------------------------------------------------------------------
// Slash command extension
// ---------------------------------------------------------------------------

const SLASH_COMMANDS = [
  { label: "Heading 1", alias: ["h1"], description: "Large section heading", exec: (e: Editor) => e.chain().focus().toggleHeading({ level: 1 }).run() },
  { label: "Heading 2", alias: ["h2"], description: "Medium section heading", exec: (e: Editor) => e.chain().focus().toggleHeading({ level: 2 }).run() },
  { label: "Heading 3", alias: ["h3"], description: "Small section heading", exec: (e: Editor) => e.chain().focus().toggleHeading({ level: 3 }).run() },
  { label: "Bullet List", alias: ["bullet", "ul"], description: "Unordered list", exec: (e: Editor) => e.chain().focus().toggleBulletList().run() },
  { label: "Numbered List", alias: ["numbered", "ol"], description: "Ordered list", exec: (e: Editor) => e.chain().focus().toggleOrderedList().run() },
  { label: "Blockquote", alias: ["quote"], description: "Capture a quote", exec: (e: Editor) => e.chain().focus().toggleBlockquote().run() },
  { label: "Code Block", alias: ["code"], description: "Code snippet", exec: (e: Editor) => e.chain().focus().toggleCodeBlock().run() },
  { label: "Divider", alias: ["divider", "hr"], description: "Horizontal rule", exec: (e: Editor) => e.chain().focus().setHorizontalRule().run() },
  {
    label: "Table", alias: ["table"], description: "Insert a table",
    exec: (e: Editor) => e.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()
  },
];

function matchSlash(query: string) {
  if (!query) return SLASH_COMMANDS;
  const q = query.toLowerCase();
  return SLASH_COMMANDS.filter(
    (c) => c.label.toLowerCase().includes(q) || c.alias.some((a) => a.startsWith(q))
  );
}

// ---------------------------------------------------------------------------
// SlashMenu component
// ---------------------------------------------------------------------------

interface SlashMenuProps {
  editor: Editor;
  query: string;
  position: { top: number; left: number };
  onClose: () => void;
}

function SlashMenu({ editor, query, position, onClose }: SlashMenuProps) {
  const [selected, setSelected] = useState(0);
  const items = matchSlash(query);

  useEffect(() => { setSelected(0); }, [query]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") { e.preventDefault(); setSelected((s) => Math.min(s + 1, items.length - 1)); }
      if (e.key === "ArrowUp") { e.preventDefault(); setSelected((s) => Math.max(s - 1, 0)); }
      if (e.key === "Enter") {
        e.preventDefault();
        if (items[selected]) {
          // Delete the /query text first
          const { from } = editor.state.selection;
          editor.chain().focus()
            .deleteRange({ from: from - query.length - 1, to: from })
            .run();
          items[selected].exec(editor);
          onClose();
        }
      }
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [items, selected, editor, query, onClose]);

  if (items.length === 0) return null;

  return (
    <div
      style={{ position: "fixed", top: position.top, left: position.left, zIndex: 9999 }}
      className="bg-white border border-slate-200 rounded-xl shadow-xl w-64 overflow-hidden"
    >
      <div className="px-3 py-2 border-b border-slate-100 text-[10px] text-slate-400 font-medium uppercase tracking-wider">
        Commands
      </div>
      <div className="max-h-64 overflow-y-auto py-1">
        {items.map((item, i) => (
          <button
            key={item.label}
            onMouseDown={(e) => {
              e.preventDefault();
              const { from } = editor.state.selection;
              editor.chain().focus()
                .deleteRange({ from: from - query.length - 1, to: from })
                .run();
              item.exec(editor);
              onClose();
            }}
            className={`w-full text-left px-3 py-2 flex items-center gap-3 transition-colors ${
              i === selected ? "bg-slate-100" : "hover:bg-slate-50"
            }`}
          >
            <div>
              <div className="text-sm font-medium text-slate-800">{item.label}</div>
              <div className="text-[10px] text-slate-400">{item.description}</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SlashCommandExtension — listens for "/" and tracks the query
// ---------------------------------------------------------------------------

function createSlashExtension(
  onOpen: (query: string, pos: { top: number; left: number }) => void,
  onClose: () => void,
  onQuery: (q: string) => void,
) {
  return Extension.create({
    name: "slashCommand",
    addProseMirrorPlugins() {
      let active = false;
      return [
        new Plugin({
          key: new PluginKey("slashCommand"),
          props: {
            handleKeyDown(view, event) {
              if (event.key === "/") {
                setTimeout(() => {
                  const { from } = view.state.selection;
                  const coords = view.coordsAtPos(from);
                  active = true;
                  onOpen("", { top: coords.bottom + 8, left: coords.left });
                  onQuery("");
                }, 0);
                return false;
              }
              if (!active) return false;
              if (event.key === " " || event.key === "Enter") {
                onClose();
                active = false;
                return false;
              }
              if (event.key === "Backspace") {
                setTimeout(() => {
                  const { from } = view.state.selection;
                  const textBefore = view.state.doc.textBetween(Math.max(0, from - 20), from);
                  const slashIdx = textBefore.lastIndexOf("/");
                  if (slashIdx === -1) { onClose(); active = false; return; }
                  const q = textBefore.slice(slashIdx + 1);
                  if (q.length === 0) { onClose(); active = false; return; }
                  onQuery(q);
                }, 0);
                return false;
              }
              // Update query on any character
              setTimeout(() => {
                const { from } = view.state.selection;
                const textBefore = view.state.doc.textBetween(Math.max(0, from - 20), from);
                const slashIdx = textBefore.lastIndexOf("/");
                if (slashIdx === -1) { onClose(); active = false; return; }
                const q = textBefore.slice(slashIdx + 1);
                const coords = view.coordsAtPos(from);
                onQuery(q);
                onOpen(q, { top: coords.bottom + 8, left: coords.left - 16 });
              }, 0);
              return false;
            },
          },
        }),
      ];
    },
  });
}

// ---------------------------------------------------------------------------
// Main editor component
// ---------------------------------------------------------------------------

interface Props {
  initialContent: Record<string, unknown>;
  onChange: (json: Record<string, unknown>, plainText: string) => void;
  onTimestampClick?: (seconds: number) => void;
  onEditorReady?: (editor: Editor) => void;
}

export default function RichTextEditor({ initialContent, onChange, onTimestampClick, onEditorReady }: Props) {
  // Keep a ref to the latest callback so the Tiptap handleClick closure always sees the current value
  const tsClickRef = useRef(onTimestampClick);
  tsClickRef.current = onTimestampClick;

  // Tiptap extension that highlights timestamps as clickable badges.
  //
  // Two cases:
  //   1. Bracketed timestamps like [03:45] or [01:23:45] anywhere in body
  //      text -- e.g. AI Summary references "I noted at [03:45]…".
  //   2. Bare HH:MM:SS or MM:SS as the WHOLE content of a tableCell --
  //      that's the polished_transcript Time column. We don't decorate
  //      bare timestamps elsewhere to avoid false positives on stuff like
  //      "ratio 12:30 to 4:15" in the user's notes.
  //
  // Both cases parse properly into total seconds for HH:MM:SS or MM:SS
  // (the prior version captured the third group but ignored it, so
  // 00:03:19 used to seek to 3 sec instead of 199 sec).
  const timestampDecoPlugin = useRef(
    new Plugin({
      key: new PluginKey("timestampDecorations"),
      props: {
        decorations(state) {
          const decorations: Decoration[] = [];
          const bracketRegex = /\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]/g;
          const wholeCellRegex = /^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$/;

          // Helper: convert capture groups to total seconds.
          // Three groups present (a:b:c) -> H:M:S
          // Two groups present (a:b)     -> M:S
          const tsToSeconds = (a: string, b: string, c?: string): number => {
            const A = parseInt(a) || 0;
            const B = parseInt(b) || 0;
            if (c !== undefined) {
              const C = parseInt(c) || 0;
              return A * 3600 + B * 60 + C;
            }
            return A * 60 + B;
          };

          const styleStr =
            "color: #4f46e5; background: #eef2ff; padding: 1px 5px; border-radius: 4px; " +
            "cursor: pointer; font-family: ui-monospace, monospace; font-size: 11px; " +
            "font-weight: 600; transition: background 0.15s;";

          // Helper: walk ancestor chain from a given doc position to see if
          // any ancestor is a tableCell. Necessary because Tiptap wraps cell
          // text in a paragraph, so descendants()'s `parent` is the paragraph,
          // not the cell. We need the GRANDPARENT (cell at depth N-2), which
          // we get by resolving the position and walking up.
          const isInsideTableCell = (textPos: number): boolean => {
            try {
              const $pos = state.doc.resolve(textPos);
              for (let d = $pos.depth; d >= 0; d--) {
                const ancestor = $pos.node(d);
                if (ancestor && ancestor.type.name === "tableCell") return true;
              }
            } catch {
              // pos might be invalid -- fall through to false
            }
            return false;
          };

          state.doc.descendants((node, pos) => {
            if (!node.isText || !node.text) return;

            // Case 1: bracketed timestamps anywhere in any text.
            let m: RegExpExecArray | null;
            while ((m = bracketRegex.exec(node.text)) !== null) {
              const from = pos + m.index;
              const to = from + m[0].length;
              const total = tsToSeconds(m[1], m[2], m[3]);
              decorations.push(
                Decoration.inline(from, to, {
                  class: "ts-seek",
                  "data-seek-seconds": String(total),
                  style: styleStr,
                }),
              );
            }

            // Case 2: bare timestamp that IS the whole content of a tableCell.
            // The polished_transcript table puts the cell text as just "00:03:19".
            // Tiptap wraps it as tableCell > paragraph > text, so we walk up
            // ancestors to see if any of them is a tableCell.
            if (isInsideTableCell(pos)) {
              const wm = wholeCellRegex.exec(node.text);
              if (wm) {
                const total = tsToSeconds(wm[1], wm[2], wm[3]);
                // Decorate the whole text node (skip leading/trailing whitespace).
                const trimStart = node.text.indexOf(wm[1]);
                const trimEnd = node.text.lastIndexOf(wm[3] ?? wm[2]) +
                                (wm[3] ?? wm[2]).length;
                decorations.push(
                  Decoration.inline(pos + trimStart, pos + trimEnd, {
                    class: "ts-seek",
                    "data-seek-seconds": String(total),
                    style: styleStr,
                  }),
                );
              }
            }
          });

          return DecorationSet.create(state.doc, decorations);
        },
      },
    }),
  ).current;

  const [slashOpen, setSlashOpen] = useState(false);
  const [slashQuery, setSlashQuery] = useState("");
  const [slashPos, setSlashPos] = useState({ top: 0, left: 0 });

  const handleOpen = useCallback((q: string, pos: { top: number; left: number }) => {
    setSlashOpen(true);
    setSlashPos(pos);
    setSlashQuery(q);
  }, []);

  const handleClose = useCallback(() => setSlashOpen(false), []);
  const handleQuery = useCallback((q: string) => setSlashQuery(q), []);

  const slashExtension = useRef(createSlashExtension(handleOpen, handleClose, handleQuery)).current;

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: false,
        // StarterKit includes: bold, italic, strike, code, codeBlock, blockquote,
        // bulletList, orderedList, listItem, horizontalRule, hardBreak, history.
        // Heading is disabled here and replaced by SectionHeading below so we
        // can persist a `sectionId` attr for the auto-inserted transcript
        // sections in Variant B (and for B-style improvements in A too).
      }),
      SectionHeading.configure({ levels: [1, 2, 3] }),
      Placeholder.configure({
        placeholder: ({ node }) => {
          if (node.type.name === "heading") return "Heading";
          return "Write your notes here, or type / for commands…";
        },
      }),
      Typography,
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
      Image.configure({ inline: false }),
      slashExtension,
      Extension.create({
        name: "timestampHighlight",
        addProseMirrorPlugins() {
          return [timestampDecoPlugin];
        },
      }),
    ],
    content: initialContent && Object.keys(initialContent).length > 0 ? initialContent : undefined,
    onUpdate: ({ editor }) => {
      onChange(editor.getJSON() as Record<string, unknown>, editor.getText());
    },
    editorProps: {
      attributes: {
        class:
          "prose prose-slate max-w-none focus:outline-none min-h-full px-10 py-8 text-sm leading-relaxed",
      },
      handleClick: (view, pos, event) => {
        const cb = tsClickRef.current;
        if (!cb) return false;

        // Check if clicked on a styled timestamp span
        const target = event.target as HTMLElement;
        if (target.classList.contains("ts-seek")) {
          const secs = parseInt(target.dataset.seekSeconds || "0");
          event.preventDefault();
          cb(secs);
          return true;
        }

        // Fallback: check text position for timestamp patterns. Bracketed
        // [HH:MM:SS] or [MM:SS] anywhere; bare HH:MM:SS / MM:SS only when
        // we're inside a tableCell (walking ancestors, since the immediate
        // parent of the text is a paragraph, not the cell itself).
        const $pos = view.state.doc.resolve(pos);
        const parentNode = $pos.parent;
        const nodeText = parentNode.textContent || "";
        const offset = $pos.parentOffset;

        let inCell = false;
        for (let d = $pos.depth; d >= 0; d--) {
          if ($pos.node(d).type.name === "tableCell") {
            inCell = true;
            break;
          }
        }
        const regex = inCell
          ? /\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?/g
          : /\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]/g;
        let m: RegExpExecArray | null;

        while ((m = regex.exec(nodeText)) !== null) {
          if (offset >= m.index - 1 && offset <= m.index + m[0].length + 1) {
            const a = parseInt(m[1]) || 0;
            const b = parseInt(m[2]) || 0;
            const c = m[3] ? parseInt(m[3]) : null;
            const totalSeconds = c !== null ? a * 3600 + b * 60 + c : a * 60 + b;
            event.preventDefault();
            cb(totalSeconds);
            return true;
          }
        }
        return false;
      },
    },
  });

  // Close slash menu on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest("[data-slash-menu]")) handleClose();
    };
    if (slashOpen) document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [slashOpen, handleClose]);

  // Notify parent container of the editor instance so it can programmatically
  // insert / replace transcript sections after Save Both.
  useEffect(() => {
    if (editor && onEditorReady) onEditorReady(editor);
  }, [editor, onEditorReady]);

  if (!editor) return null;

  return (
    <div className="relative h-full flex flex-col">
      {/* Bubble menu — appears on text selection */}
      <BubbleMenu
        editor={editor}
        tippyOptions={{ duration: 100 }}
        className="flex items-center gap-0.5 bg-slate-900 rounded-lg px-1.5 py-1 shadow-xl"
      >
        {[
          { icon: Bold, title: "Bold (⌘B)", action: () => editor.chain().focus().toggleBold().run(), active: editor.isActive("bold") },
          { icon: Italic, title: "Italic (⌘I)", action: () => editor.chain().focus().toggleItalic().run(), active: editor.isActive("italic") },
          { icon: Strikethrough, title: "Strikethrough", action: () => editor.chain().focus().toggleStrike().run(), active: editor.isActive("strike") },
          { icon: Code, title: "Inline code", action: () => editor.chain().focus().toggleCode().run(), active: editor.isActive("code") },
          { icon: List, title: "Bullet list", action: () => editor.chain().focus().toggleBulletList().run(), active: editor.isActive("bulletList") },
          { icon: ListOrdered, title: "Numbered list", action: () => editor.chain().focus().toggleOrderedList().run(), active: editor.isActive("orderedList") },
          { icon: Quote, title: "Blockquote", action: () => editor.chain().focus().toggleBlockquote().run(), active: editor.isActive("blockquote") },
        ].map(({ icon: Icon, title, action, active }) => (
          <button
            key={title}
            onMouseDown={(e) => { e.preventDefault(); action(); }}
            title={title}
            className={`p-1.5 rounded-md transition-colors ${
              active ? "bg-white text-slate-900" : "text-slate-300 hover:text-white hover:bg-slate-700"
            }`}
          >
            <Icon size={14} />
          </button>
        ))}
      </BubbleMenu>

      {/* Editor */}
      <div className="flex-1 overflow-y-auto">
        <EditorContent editor={editor} className="h-full" />
      </div>

      {/* Slash command menu */}
      {slashOpen && (
        <div data-slash-menu>
          <SlashMenu
            editor={editor}
            query={slashQuery}
            position={slashPos}
            onClose={handleClose}
          />
        </div>
      )}
    </div>
  );
}
