"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface Props {
  /** "image": one zoomable image. "pdf": embedded PDF viewer. "pages": legacy page images. */
  mode: "image" | "pdf" | "pages";
  /** blob URL of loaded content (single mode) */
  src?: string;
  /** blob URLs (pages mode) */
  srcs?: string[];
  filename?: string;
  title?: string;
  loading?: boolean;
  onClose: () => void;
}

const MIN_ZOOM = 1;
const MAX_ZOOM = 10;
const clamp = (z: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

const toolBtn =
  "rounded-lg px-2.5 py-1.5 text-sm font-semibold text-white/90 hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-white/40 disabled:opacity-40";

/** Fullscreen receipt viewer. Nothing renders until the parent has fetched the
 *  blob (on-demand), so the claim page itself stays lazy. Zoom via buttons,
 *  +/- keys and mouse wheel; drag to pan; download; Esc/backdrop closes. */
export function ReceiptViewer({ mode, src, srcs, filename, title, loading, onClose }: Props) {
  const pages = srcs || (src ? [src] : []);
  const [pageIdx, setPageIdx] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);

  const resetView = useCallback(() => { setZoom(1); setOffset({ x: 0, y: 0 }); }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "+" || e.key === "=") setZoom((z) => clamp(z + 0.5));
      if (e.key === "-") setZoom((z) => clamp(z - 0.5));
      if (e.key === "ArrowRight") setPageIdx((i) => Math.min(i + 1, pages.length - 1));
      if (e.key === "ArrowLeft") setPageIdx((i) => Math.max(i - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose, pages.length]);

  useEffect(() => { resetView(); setPageIdx(0); }, [mode, src, resetView]);

  // wheel zoom (non-passive so page scroll is prevented inside the viewer)
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const onWheel = (e: WheelEvent) => {
      if (mode === "pdf") return; // let the PDF viewer scroll itself
      e.preventDefault();
      setZoom((z) => clamp(z * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  }, [mode]);

  const applyZoom = (next: number) => {
    setZoom(clamp(next));
    if (next <= 1) setOffset({ x: 0, y: 0 });
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (zoom <= 1 || mode === "pdf") return;
    setDragging(true);
    dragStart.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragging || !dragStart.current) return;
    setOffset({
      x: dragStart.current.ox + (e.clientX - dragStart.current.x),
      y: dragStart.current.oy + (e.clientY - dragStart.current.y),
    });
  };
  const endDrag = () => { setDragging(false); dragStart.current = null; };

  const download = () => {
    const url = mode === "pages" ? pages[pageIdx] : src;
    if (!url) return;
    const a = document.createElement("a");
    a.href = url;
    const name = filename || "receipt";
    a.download = pages.length > 1 ? name.replace(/(\.[a-z0-9]+)?$/i, `-page-${pageIdx + 1}$1`) : name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const showPageNav = pages.length > 1;
  const current = pages[Math.min(pageIdx, Math.max(0, pages.length - 1))];

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-slate-950/95 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Original receipt viewer"
      onClick={onClose}
    >
      {/* toolbar */}
      <div
        className="flex items-center justify-between gap-2 border-b border-white/10 px-4 py-2.5"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="min-w-0 truncate text-sm font-semibold text-white/80">
          {title || "Original receipt"}
          {showPageNav && <span className="ml-2 text-white/50">page {pageIdx + 1}/{pages.length}</span>}
        </p>
        <div className="flex shrink-0 items-center gap-1">
          {showPageNav && (
            <>
              <button type="button" className={toolBtn} aria-label="Previous page"
                onClick={() => setPageIdx((i) => Math.max(0, i - 1))} disabled={pageIdx === 0}>‹ Prev</button>
              <button type="button" className={toolBtn} aria-label="Next page"
                onClick={() => setPageIdx((i) => Math.min(pages.length - 1, i + 1))}
                disabled={pageIdx >= pages.length - 1}>Next ›</button>
              <span className="mx-1 h-5 w-px bg-white/20" />
            </>
          )}
          {mode !== "pdf" && (
            <>
              <button type="button" className={toolBtn} aria-label="Zoom out"
                onClick={() => applyZoom(zoom - 0.5)} disabled={zoom <= MIN_ZOOM}>−</button>
              <span className="w-14 text-center text-xs font-bold tabular-nums text-white/70">{Math.round(zoom * 100)}%</span>
              <button type="button" className={toolBtn} aria-label="Zoom in"
                onClick={() => applyZoom(zoom + 0.5)} disabled={zoom >= MAX_ZOOM}>+</button>
              <button type="button" className={toolBtn} aria-label="Reset zoom"
                onClick={() => { setZoom(1); setOffset({ x: 0, y: 0 }); }}>Fit</button>
              <span className="mx-1 h-5 w-px bg-white/20" />
            </>
          )}
          <button type="button" className={toolBtn} onClick={download} disabled={!src && !srcs?.length}
            title="Download the original file">⬇ Download</button>
          <button type="button" className={`${toolBtn} text-lg`} aria-label="Close viewer"
            onClick={onClose}>×</button>
        </div>
      </div>

      {/* stage */}
      <div
        ref={stageRef}
        className="flex flex-1 items-center justify-center overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {loading && (
          <div className="flex flex-col items-center gap-3 text-white/80">
            <span className="h-8 w-8 animate-spin rounded-full border-2 border-white/30 border-t-white" aria-hidden />
            <p className="text-sm">Loading receipt…</p>
          </div>
        )}
        {!loading && mode === "pdf" && src && (
          <iframe
            src={src}
            title="Receipt PDF"
            sandbox="allow-same-origin"
            className="h-[88vh] w-[94vw] rounded-sm bg-white shadow-2xl"
          />
        )}
        {!loading && mode !== "pdf" && current && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={current}
            alt="Original receipt"
            loading="lazy"
            draggable={false}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            className={`max-h-[85vh] max-w-[95vw] origin-center rounded-sm shadow-2xl select-none ${dragging ? "cursor-grabbing" : zoom > 1 ? "cursor-grab" : "cursor-zoom-in"}`}
            style={{
              transform: `scale(${zoom}) translate(${offset.x / zoom}px, ${offset.y / zoom}px)`,
              transition: dragging ? "none" : "transform 120ms ease-out",
            }}
          />
        )}
      </div>
      {!loading && (
        <p className="pb-3 text-center text-xs text-white/50">
          {mode === "pdf"
            ? "Embedded PDF viewer · Download exports the original file · Esc closes"
            : "Scroll to zoom · drag to pan · Esc or click outside closes"}
        </p>
      )}
    </div>
  );
}
