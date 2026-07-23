"use client";

import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

type AnnotationTool = "freehand" | "rectangle" | "circle" | "arrow";
interface Point { x: number; y: number }
interface AnnotationPath { id: string; type: AnnotationTool; color: string; points: Point[]; start: Point; end: Point }

const TOOLS: Array<{ id: AnnotationTool; label: string; icon: string }> = [
  { id: "freehand", label: "Dibujar", icon: "✎" },
  { id: "rectangle", label: "Rectángulo", icon: "▭" },
  { id: "circle", label: "Círculo", icon: "○" },
  { id: "arrow", label: "Flecha", icon: "↗" },
];
const COLORS = ["#ef4444", "#f59e0b", "#10b981", "#3b82f6"];

function description(paths: AnnotationPath[]): string {
  const names: Record<AnnotationTool, string> = { freehand: "trazo", rectangle: "rectángulo", circle: "círculo", arrow: "flecha" };
  return paths.map((path, index) => `${index + 1}. ${names[path.type]} ${path.color} cerca de (${Math.round(path.end.x)}, ${Math.round(path.end.y)})`).join("; ");
}

export function CanvasAnnotations({ width, height, active, onChange }: { width: number; height: number; active: boolean; onChange: (description: string) => void }) {
  const [paths, setPaths] = useState<AnnotationPath[]>([]);
  const [tool, setTool] = useState<AnnotationTool>("freehand");
  const [color, setColor] = useState(COLORS[0]);
  const drawing = useRef<AnnotationPath | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  function point(event: ReactPointerEvent<SVGSVGElement>): Point {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return { x: ((event.clientX - rect.left) / rect.width) * width, y: ((event.clientY - rect.top) / rect.height) * height };
  }
  function publish(update: (current: AnnotationPath[]) => AnnotationPath[]) {
    setPaths((current) => {
      const next = update(current);
      onChange(description(next));
      return next;
    });
  }
  function onPointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    if (!active) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const start = point(event);
    drawing.current = { id: `annotation-${Date.now()}-${paths.length}`, type: tool, color, points: [start], start, end: start };
    const path = drawing.current;
    publish((current) => [...current, path]);
  }
  function onPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    if (!drawing.current) return;
    const end = point(event);
    const current = { ...drawing.current, end, points: drawing.current.type === "freehand" ? [...drawing.current.points, end] : drawing.current.points };
    drawing.current = current;
    publish((paths) => paths.map((path) => path.id === current.id ? current : path));
  }
  function onPointerUp() { drawing.current = null; }

  if (!active && paths.length === 0) return null;
  return (
    <div className={`absolute inset-0 ${active ? "pointer-events-auto" : "pointer-events-none"}`}>
      {active && <div className="absolute left-2 top-2 z-20 flex flex-wrap items-center gap-1 rounded-xl border border-slate-200 bg-white/95 p-1.5 shadow-lg backdrop-blur dark:border-slate-700 dark:bg-slate-900/95">
        {TOOLS.map((item) => <button key={item.id} type="button" title={item.label} aria-label={item.label} onClick={() => setTool(item.id)} className={`flex h-7 w-7 items-center justify-center rounded text-sm ${tool === item.id ? "bg-brand-600 text-white" : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"}`}>{item.icon}</button>)}
        <span className="mx-0.5 h-5 w-px bg-slate-200 dark:bg-slate-700" />
        {COLORS.map((item) => <button key={item} type="button" title={`Color ${item}`} aria-label={`Color ${item}`} onClick={() => setColor(item)} className={`h-5 w-5 rounded-full border-2 ${color === item ? "border-slate-900 dark:border-white" : "border-white dark:border-slate-900"}`} style={{ background: item }} />)}
        <button type="button" className="ml-1 px-1 text-[10px] text-slate-500 hover:text-rose-500" onClick={() => publish(() => [])}>Limpiar</button>
      </div>}
      <svg ref={svgRef} viewBox={`0 0 ${width} ${height}`} className="h-full w-full touch-none" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} aria-label="Anotaciones sobre el diseño">
        <defs><marker id="studio-arrow" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="context-stroke" /></marker></defs>
        {paths.map((path) => {
          const common = { fill: "none", stroke: path.color, strokeWidth: 4, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
          if (path.type === "freehand") return <polyline key={path.id} points={path.points.map((item) => `${item.x},${item.y}`).join(" ")} {...common} />;
          if (path.type === "rectangle") return <rect key={path.id} x={Math.min(path.start.x, path.end.x)} y={Math.min(path.start.y, path.end.y)} width={Math.abs(path.end.x - path.start.x)} height={Math.abs(path.end.y - path.start.y)} {...common} />;
          if (path.type === "circle") return <ellipse key={path.id} cx={(path.start.x + path.end.x) / 2} cy={(path.start.y + path.end.y) / 2} rx={Math.abs(path.end.x - path.start.x) / 2} ry={Math.abs(path.end.y - path.start.y) / 2} {...common} />;
          return <line key={path.id} x1={path.start.x} y1={path.start.y} x2={path.end.x} y2={path.end.y} markerEnd="url(#studio-arrow)" {...common} />;
        })}
      </svg>
    </div>
  );
}
