"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { earningsClient, type EarningsReleaseDetail } from "@/lib/api/earningsClient";
import PressReleaseView from "./PressReleaseView";

interface Props {
  releaseId: string;
}

export default function PressReleaseContainer({ releaseId }: Props) {
  const router = useRouter();
  const [release, setRelease] = useState<EarningsReleaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    earningsClient.get(releaseId)
      .then((res) => {
        if (res.success && res.data) {
          setRelease(res.data);
        } else {
          setError("Press release not found");
        }
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  }, [releaseId]);

  return (
    <PressReleaseView
      release={release}
      loading={loading}
      error={error}
      onBack={() => router.push("/notes")}
    />
  );
}
