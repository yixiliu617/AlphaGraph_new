import Heading from "@tiptap/extension-heading";

/**
 * SectionHeading — TipTap Heading extended with a stable `sectionId` attr.
 *
 * Persisted to HTML as `data-section-id`. Used by the editor-section builder
 * to find an existing section heading and replace the content that follows
 * it, without resorting to matching on visible heading text (which the user
 * is allowed to edit).
 *
 * Only intended for h2 nodes in practice; the attribute is optional and
 * defaults to null for all other headings.
 */
export const SectionHeading = Heading.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      sectionId: {
        default: null,
        parseHTML: (el: HTMLElement) => el.getAttribute("data-section-id"),
        renderHTML: (attrs: Record<string, unknown>) =>
          attrs.sectionId ? { "data-section-id": String(attrs.sectionId) } : {},
      },
    };
  },
});

/** Valid section ids used by the editor auto-insert. */
export type SectionId = "user_notes" | "raw_transcript" | "polished_transcript";
