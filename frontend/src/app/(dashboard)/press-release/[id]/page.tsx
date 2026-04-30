import PressReleaseContainer from "./PressReleaseContainer";

// Next.js 15: dynamic route params is now a Promise. Must `await` it.
// https://nextjs.org/blog/next-15#async-request-apis-breaking-change
interface Props {
  params: Promise<{ id: string }>;
}

export default async function PressReleasePage({ params }: Props) {
  const { id } = await params;
  return <PressReleaseContainer releaseId={decodeURIComponent(id)} />;
}
