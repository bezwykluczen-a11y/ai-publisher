"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

type Props = {
  runId: number;
  status: string;
};

export default function RunActions({ runId, status }: Props) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  async function updateStatus(nextStatus: "running" | "completed") {
    try {
      setLoading(true);

      const res = await fetch(
        `http://localhost:8000/runs/${runId}/status?status=${nextStatus}`,
        {
          method: "POST",
        }
      );

      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "Failed to update run status");
      }

      router.refresh();
    } catch (err) {
      console.error(err);
      alert("Failed to update run status");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
      {status === "queued" && (
        <button onClick={() => updateStatus("running")} disabled={loading}>
          {loading ? "Updating..." : "Start"}
        </button>
      )}

      {status === "running" && (
        <button onClick={() => updateStatus("completed")} disabled={loading}>
          {loading ? "Updating..." : "Complete"}
        </button>
      )}
    </div>
  );
}