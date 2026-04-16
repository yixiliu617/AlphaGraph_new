import NotesEditorContainer from "./NotesEditorContainer";

interface Props {
  params: { id: string };
}

export default function NoteEditorPage({ params }: Props) {
  return <NotesEditorContainer noteId={params.id} />;
}
