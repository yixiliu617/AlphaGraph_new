import PressReleaseContainer from "./PressReleaseContainer";

interface Props {
  params: { id: string };
}

export default function PressReleasePage({ params }: Props) {
  return <PressReleaseContainer releaseId={decodeURIComponent(params.id)} />;
}
