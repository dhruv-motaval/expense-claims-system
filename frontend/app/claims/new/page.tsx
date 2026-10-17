"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/auth";
import { ErrorBox, Shell } from "@/components/ui";
import { inr } from "@/lib/format";

const CATEGORIES = ["TRAVEL", "MEALS", "SUPPLIES", "TAXI", "ACCOMMODATION", "OTHER"];

interface Match {
  claim_id: number; claim_number: string; user_name: string; merchant: string;
  amount: number; date: string; score: number; level: string;
}

export default function NewClaimPage() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState<any>(null);
  const [matches, setMatches] = useState<Match[]>([]);
  const [override, setOverride] = useState(false);
  const [receiptId, setReceiptId] = useState<number | null>(null);

  useEffect(() => {
    if (!file) { setPreview(""); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  async function extractText() {
    setBusy("extract"); setError(""); setMatches([]); setOverride(false);
    try {
      const body = new FormData();
      body.append("text", text);
      const d = await api("/receipts/extract", { method: "POST", body });
      setForm({
        merchant: d.merchant || "", transaction_date: d.date || "",
        category: d.category || "OTHER", amount: d.amount ?? "",
        currency: d.currency || "INR", description: d.description || "",
      });
      setMatches(d.duplicate_matches || []);
      setReceiptId(d.receipt_id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function uploadImage() {
    if (!file) return;
    setBusy("extract"); setError(""); setMatches([]); setOverride(false);
    try {
      const body = new FormData();
      body.append("file", file);
      const d = await api("/receipts/upload", { method: "POST", body });
      setForm({
        merchant: d.merchant || "", transaction_date: d.date || "",
        category: d.category || "OTHER", amount: d.amount ?? "",
        currency: d.currency || "INR", description: d.description || "",
      });
      setMatches(d.duplicate_matches || []);
      setReceiptId(d.receipt_id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function save(submit: boolean) {
    setBusy(submit ? "submit" : "draft"); setError("");
    try {
      const d = await api("/claims", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, amount: Number(form.amount), receipt_id: receiptId }),
      });
      if (submit) await api(`/claims/${d.id}/submit`, { method: "POST" });
      router.push(`/claims/${d.id}`);
    } catch (e: any) {
      setError(e.message);
      setBusy("");
    }
  }

  const strong = matches.some((m) => m.level === "strong");

  return (
    <Shell>
      <h1 className="text-xl font-bold mb-4">New Claim</h1>

      {!form && (
        <div className="space-y-4 max-w-xl">
          <div className="bg-white rounded-lg shadow p-4 space-y-3">
            <label htmlFor="receipt-text" className="block text-sm font-medium">Paste receipt text</label>
            <textarea
              id="receipt-text" rows={5} value={text} onChange={(e) => setText(e.target.value)}
              placeholder={"UBER\n12 Aug 2026\nTrip to Airport\n₹320"}
              className="w-full border rounded px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              onClick={extractText} disabled={busy === "extract" || !text.trim()}
              className="bg-blue-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              {busy === "extract" ? "Extracting..." : "Extract Claim"}
            </button>
          </div>
          <div className="bg-white rounded-lg shadow p-4 space-y-3">
            <label htmlFor="receipt-file" className="block text-sm font-medium">Or upload receipt file (pdf/jpg/png/webp, max 10 MB)</label>
            <input
              id="receipt-file" type="file" accept="application/pdf,image/jpeg,image/png,image/webp,.pdf,.jpg,.jpeg,.png,.webp"
              onChange={(e) => {
                const f = e.target.files?.[0] || null;
                setError("");
                if (f) {
                  const okType = f.type === "application/pdf" || ["image/jpeg", "image/png", "image/webp"].includes(f.type)
                    || /\.(pdf|jpe?g|png|webp)$/i.test(f.name);
                  if (!okType) { setError("Unsupported file. Upload a PDF, JPG, PNG or WebP receipt."); return; }
                  if (f.size > 10 * 1024 * 1024) { setError("File too large. Max size is 10 MB."); return; }
                  if (f.size === 0) { setError("The selected file is empty."); return; }
                }
                setFile(f);
              }}
              className="block text-sm"
            />
            {file && (
              <div className="flex items-center gap-3 border rounded p-2 bg-gray-50">
                {preview && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={preview} alt="Receipt preview" className="h-14 w-14 object-cover rounded border" />
                )}
                <div className="flex-1 min-w-0 text-sm">
                  <p className="truncate font-medium">{file.name}</p>
                  <p className="text-xs text-gray-500">
                    {file.type === "application/pdf" || /\.pdf$/i.test(file.name) ? "PDF document · " : ""}
                    {(file.size / 1024).toFixed(0)} KB
                  </p>
                </div>
                <button
                  onClick={() => { setFile(null); setPreview(""); if (fileInputRef.current) fileInputRef.current.value = ""; }}
                  disabled={busy === "extract"}
                  aria-label="Remove selected file"
                  className="text-red-600 hover:text-red-700 text-sm font-medium px-2 py-1 rounded hover:bg-red-50 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-red-400"
                >
                  Remove
                </button>
              </div>
            )}
            <button
              onClick={uploadImage} disabled={busy === "extract" || !file}
              className="bg-blue-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              {busy === "extract"
                ? (file && (file.type === "application/pdf" || /\.pdf$/i.test(file.name))
                  ? "Processing PDF (extract text, render pages)..." : "Uploading & extracting...")
                : "Upload & Extract"}
            </button>
          </div>
          <ErrorBox message={error} />
        </div>
      )}

      {form && (
        <div className="max-w-xl space-y-4">
          {matches.length > 0 && !override && (
            <div className={`rounded-lg border p-4 ${strong ? "bg-red-50 border-red-300" : "bg-amber-50 border-amber-300"}`}>
              <p className="font-semibold text-sm">Possible duplicate receipt</p>
              <p className="text-xs text-gray-600 mt-1">We found existing claims that look similar:</p>
              <ul className="mt-2 space-y-1 text-sm">
                {matches.slice(0, 3).map((m) => (
                  <li key={m.claim_id} className="flex justify-between">
                    <Link href={`/claims/${m.claim_id}`} className="text-blue-600 hover:underline">
                      {m.claim_number} — {m.merchant} — {inr(m.amount)} — {m.date} ({m.user_name})
                    </Link>
                    <span className={m.level === "strong" ? "text-red-700 font-semibold" : "text-amber-700"}>
                      {m.score}% {m.level}
                    </span>
                  </li>
                ))}
              </ul>
              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => setOverride(true)}
                  className="bg-gray-800 text-white rounded px-3 py-1.5 text-xs font-medium hover:bg-gray-900"
                >
                  Continue Anyway
                </button>
                <button
                  onClick={() => setForm(null)}
                  className="border rounded px-3 py-1.5 text-xs font-medium hover:bg-gray-100"
                >
                  Discard
                </button>
              </div>
            </div>
          )}

          <div className="bg-white rounded-lg shadow p-4 space-y-3">
            <p className="text-xs font-semibold text-gray-500 uppercase">Review & edit extracted claim</p>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-sm">
                <span className="block font-medium mb-1">Merchant</span>
                <input value={form.merchant} onChange={(e) => setForm({ ...form, merchant: e.target.value })}
                  className="w-full border rounded px-3 py-2" required />
              </label>
              <label className="text-sm">
                <span className="block font-medium mb-1">Date</span>
                <input type="date" value={form.transaction_date}
                  onChange={(e) => setForm({ ...form, transaction_date: e.target.value })}
                  className="w-full border rounded px-3 py-2" required />
              </label>
              <label className="text-sm">
                <span className="block font-medium mb-1">Category</span>
                <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
                  className="w-full border rounded px-3 py-2">
                  {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
                </select>
              </label>
              <label className="text-sm">
                <span className="block font-medium mb-1">Amount (₹)</span>
                <input type="number" min="0.01" step="0.01" value={form.amount}
                  onChange={(e) => setForm({ ...form, amount: e.target.value })}
                  className="w-full border rounded px-3 py-2" required />
              </label>
            </div>
            <label className="text-sm block">
              <span className="block font-medium mb-1">Description</span>
              <input value={form.description || ""} onChange={(e) => setForm({ ...form, description: e.target.value })}
                className="w-full border rounded px-3 py-2" />
            </label>
            <ErrorBox message={error} />
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => save(false)} disabled={!!busy}
                className="border rounded px-4 py-2 text-sm font-medium hover:bg-gray-100 disabled:opacity-50"
              >
                {busy === "draft" ? "Saving..." : "Save Draft"}
              </button>
              <button
                onClick={() => save(true)} disabled={!!busy || (matches.length > 0 && !override)}
                title={matches.length > 0 && !override ? "Acknowledge the duplicate warning first" : undefined}
                className="bg-blue-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {busy === "submit" ? "Submitting..." : "Submit Claim"}
              </button>
            </div>
          </div>
        </div>
      )}
    </Shell>
  );
}
