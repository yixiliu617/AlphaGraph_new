import NotesEditorContainer from "./NotesEditorContainer";

// Next.js 15 changed dynamic route params to a Promise. Must `await` it.
// https://nextjs.org/blog/next-15#async-request-apis-breaking-change
interface Props {
  params: Promise<{ id: string }>;
}

export default async function NoteEditorPage({ params }: Props) {
  const { id } = await params;
  return <NotesEditorContainer noteId={id} />;
}
