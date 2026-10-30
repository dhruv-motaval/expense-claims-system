export function inr(n: number | string | null | undefined): string {
  const v = Number(n ?? 0);
  return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

export function fmtDate(d: string | null | undefined): string {
  if (!d) return "—";
  return new Date(d + (d.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-IN", {
    day: "numeric", month: "short", year: "numeric",
  });
}

export function fmtDateTime(d: string | null | undefined): string {
  if (!d) return "—";
  return new Date(d).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export const STATUS_COLORS: Record<string, string> = {
  DRAFT: "bg-gray-100 text-gray-700",
  SUBMITTED: "bg-blue-100 text-blue-800",
  APPROVED: "bg-emerald-100 text-emerald-800",
  REJECTED: "bg-red-100 text-red-800",
  PAID: "bg-violet-100 text-violet-800",
  SENT_BACK: "bg-orange-100 text-orange-800",
};

export const STATUS_LABELS: Record<string, string> = {
  DRAFT: "Draft",
  SUBMITTED: "Submitted",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  PAID: "Paid",
  SENT_BACK: "Returned for revision",
};

/** Duplicate tier chips: identity tiers block/gate, D is advisory only. */
export const TIER_META: Record<string, { label: string; cls: string; title: string }> = {
  A: { label: "Duplicate A · same file", cls: "bg-red-100 text-red-800", title: "The exact same receipt file has already been paid (checksum match). Payment is blocked." },
  B: { label: "Duplicate B · same receipt no.", cls: "bg-red-100 text-red-800", title: "This receipt number has already been paid. Payment is blocked." },
  C: { label: "Duplicate C · same transaction", cls: "bg-amber-100 text-amber-800", title: "Same merchant, date and amount were already paid. Payment requires a typed justification." },
  D: { label: "Possible duplicate", cls: "bg-amber-50 text-amber-700", title: "Strong textual similarity to an existing claim. Advisory only — does not block payment." },
};

export function tierMeta(tier: string | null | undefined) {
  return tier ? TIER_META[tier] : null;
}
