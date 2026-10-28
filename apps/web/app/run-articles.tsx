"use client";

import { useState } from "react";

type Article = {
  id: number;
  run_id: number;
  title: string;
  content: string;
  llm_provider: string;
  llm_model: string;
  created_at: string;
};

type Props = {
  runId: number;
};

export default function RunArticles({ runId }: Props) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [articles, setArticles] = useState<Article[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggleOpen() {
    if (open) {
      setOpen(false);
      return;
    }

    setOpen(true);

    if (articles !== null) {
      // Already loaded once — no need to refetch.
      return;
    }

    try {
      setLoading(true);
      setError(null);

      const res = await fetch(`http://localhost:8000/runs/${runId}/articles`);

      if (!res.ok) {
        throw new Error("Failed to load articles");
      }

      const data = await res.json();
      setArticles(data);
    } catch (err) {
      console.error(err);
      setError("Could not load articles for this run.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ marginTop: 8 }}>
      <button onClick={toggleOpen} style={{ fontSize: 13 }}>
        {open ? "Hide articles" : "Show articles"}
      </button>

      {open && (
        <div
          style={{
            marginTop: 8,
            paddingLeft: 12,
            borderLeft: "2px solid #ddd",
          }}
        >
          {loading && <p style={{ fontSize: 13, color: "#666" }}>Loading articles...</p>}

          {error && <p style={{ fontSize: 13, color: "#a12c2c" }}>{error}</p>}

          {!loading && !error && articles !== null && articles.length === 0 && (
            <p style={{ fontSize: 13, color: "#666" }}>No articles generated for this run.</p>
          )}

          {!loading && !error && articles !== null && articles.length > 0 && (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {articles.map((article) => (
                <li
                  key={article.id}
                  style={{
                    marginBottom: 12,
                    paddingBottom: 12,
                    borderBottom: "1px solid #eee",
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{article.title}</div>
                  <div style={{ fontSize: 12, color: "#888", marginBottom: 6 }}>
                    {article.llm_provider} / {article.llm_model} — {article.created_at}
                  </div>
                  <div
                    style={{
                      fontSize: 13,
                      whiteSpace: "pre-wrap",
                      color: "#333",
                    }}
                  >
                    {article.content}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
