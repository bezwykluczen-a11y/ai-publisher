"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function NewProjectForm() {
  const router = useRouter();

  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [keywords, setKeywords] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setSuccess(false);

    try {
      const res = await fetch("http://localhost:8000/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          domain: domain.trim(),
          keywords: keywords.trim() || null,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || "Failed to create project.");
      }

      setName("");
      setDomain("");
      setKeywords("");
      setSuccess(true);
      router.refresh();
    } catch (err: any) {
      setError(err.message || "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      style={{
        marginBottom: 24,
        padding: 16,
        border: "1px solid #ddd",
        borderRadius: 6,
        maxWidth: 480,
      }}
    >
      <h3 style={{ marginTop: 0 }}>New project</h3>

      <div style={{ marginBottom: 8 }}>
        <label style={{ display: "block", fontSize: 13, marginBottom: 4 }}>
          Name
        </label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          style={{ width: "100%", padding: 6 }}
        />
      </div>

      <div style={{ marginBottom: 8 }}>
        <label style={{ display: "block", fontSize: 13, marginBottom: 4 }}>
          Domain
        </label>
        <input
          type="text"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          required
          placeholder="example.com"
          style={{ width: "100%", padding: 6 }}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <label style={{ display: "block", fontSize: 13, marginBottom: 4 }}>
          Keywords (optional)
        </label>
        <input
          type="text"
          value={keywords}
          onChange={(e) => setKeywords(e.target.value)}
          placeholder="seo, marketing, content"
          style={{ width: "100%", padding: 6 }}
        />
      </div>

      <button type="submit" disabled={submitting}>
        {submitting ? "Creating..." : "Create project"}
      </button>

      {error && (
        <p style={{ color: "#a12c2c", fontSize: 13, marginTop: 8 }}>{error}</p>
      )}
      {success && (
        <p style={{ color: "#2c7a2c", fontSize: 13, marginTop: 8 }}>
          Project created successfully.
        </p>
      )}
    </form>
  );
}
