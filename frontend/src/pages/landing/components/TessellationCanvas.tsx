import { useEffect, useRef } from 'react';
import { usePrefersReducedMotion } from '../hooks';

interface TessellationCanvasProps {
  className?: string;
  /** Grid spacing in CSS px. */
  spacing?: number;
  /** Cursor influence radius in CSS px. */
  radius?: number;
  /** Max vertex displacement in CSS px at the cursor. */
  maxDisplacement?: number;
}

interface MeshPoint {
  baseX: number;
  baseY: number;
  jitterX: number;
  jitterY: number;
  phase: number;
  x: number;
  y: number;
}

interface Mesh {
  points: MeshPoint[];
  /** Flat triangle index list, 3 entries per triangle. */
  triangles: number[];
  width: number;
  height: number;
}

function buildMesh(width: number, height: number, spacing: number): Mesh {
  const cols = Math.ceil(width / spacing) + 3;
  const rows = Math.ceil(height / spacing) + 3;
  const points: MeshPoint[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const offset = r % 2 === 1 ? spacing / 2 : 0;
      points.push({
        baseX: c * spacing + offset - spacing,
        baseY: r * spacing - spacing,
        jitterX: (Math.random() - 0.5) * spacing * 0.55,
        jitterY: (Math.random() - 0.5) * spacing * 0.55,
        phase: Math.random() * Math.PI * 2,
        x: 0,
        y: 0,
      });
    }
  }
  // Offset-grid triangulation: each cell splits into two triangles.
  const triangles: number[] = [];
  for (let r = 0; r < rows - 1; r++) {
    for (let c = 0; c < cols - 1; c++) {
      const i = r * cols + c;
      if (r % 2 === 0) {
        triangles.push(i, i + 1, i + cols, i + 1, i + cols + 1, i + cols);
      } else {
        triangles.push(i, i + 1, i + cols + 1, i, i + cols + 1, i + cols);
      }
    }
  }
  return { points, triangles, width, height };
}

/**
 * Interactive triangular-mesh hero background: vertices repel from the cursor
 * with eased trailing, idle sine drift otherwise. Hand-rolled Canvas 2D — no
 * mesh library. Renders one static frame under prefers-reduced-motion; the
 * rAF loop only runs while the canvas is on screen and the tab is visible.
 */
export default function TessellationCanvas({
  className = '',
  spacing = 90,
  radius = 260,
  maxDisplacement = 22,
}: TessellationCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reducedMotion = usePrefersReducedMotion();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let mesh: Mesh | null = null;
    let raf = 0;
    let running = false;
    let onScreen = true;
    let frame = 0;
    let lastPointerMove = 0;
    let hasPointer = false;
    const target = { x: -10000, y: -10000 };
    const mouse = { x: -10000, y: -10000 };

    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      mesh = buildMesh(rect.width, rect.height, spacing);
      if (reducedMotion) drawFrame(performance.now());
    };

    const drawFrame = (now: number) => {
      if (!mesh) return;
      const t = now / 1000;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, mesh.width, mesh.height);

      // Virtual cursor for touch / pointerless sessions: slow Lissajous path.
      let cx = mouse.x;
      let cy = mouse.y;
      if (!hasPointer && !reducedMotion) {
        cx = mesh.width * (0.5 + 0.38 * Math.sin(t * 0.23));
        cy = mesh.height * (0.5 + 0.34 * Math.sin(t * 0.31 + 1.3));
      }

      const { points, triangles } = mesh;
      for (const p of points) {
        const drift = reducedMotion ? 0 : 2;
        const jx = p.baseX + p.jitterX + Math.sin(t * 0.3 + p.phase) * drift;
        const jy = p.baseY + p.jitterY + Math.cos(t * 0.27 + p.phase) * drift;
        const dx = jx - cx;
        const dy = jy - cy;
        const d = Math.hypot(dx, dy);
        const influence = d < radius ? (1 - d / radius) ** 2 : 0;
        if (influence > 0 && d > 0.001) {
          p.x = jx + (dx / d) * influence * maxDisplacement;
          p.y = jy + (dy / d) * influence * maxDisplacement;
        } else {
          p.x = jx;
          p.y = jy;
        }
      }

      ctx.lineWidth = 1;
      ctx.lineJoin = 'round';
      for (let i = 0; i < triangles.length; i += 3) {
        const a = points[triangles[i]];
        const b = points[triangles[i + 1]];
        const c = points[triangles[i + 2]];
        const centroidX = (a.x + b.x + c.x) / 3;
        const centroidY = (a.y + b.y + c.y) / 3;
        const d = Math.hypot(centroidX - cx, centroidY - cy);
        const influence = d < radius ? (1 - d / radius) ** 2 : 0;

        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.lineTo(c.x, c.y);
        ctx.closePath();

        // Navy-mid fill brightening near the cursor; hairline strokes with a
        // brand-blue glint on the closest triangles. No red in the mesh.
        ctx.fillStyle = `rgba(31, 56, 100, ${(0.1 + influence * 0.32).toFixed(3)})`;
        ctx.fill();
        if (influence > 0.45) {
          ctx.strokeStyle = `rgba(91, 155, 212, ${(0.15 + influence * 0.4).toFixed(3)})`;
        } else {
          ctx.strokeStyle = `rgba(255, 255, 255, ${(0.035 + influence * 0.14).toFixed(3)})`;
        }
        ctx.stroke();
      }
    };

    const tick = (now: number) => {
      raf = 0;
      if (!running) return;
      frame++;
      // Idle throttle: after 3s without pointer movement, draw alternate frames.
      const idle = hasPointer && now - lastPointerMove > 3000;
      if (!idle || frame % 2 === 0) {
        mouse.x += (target.x - mouse.x) * 0.08;
        mouse.y += (target.y - mouse.y) * 0.08;
        drawFrame(now);
      }
      raf = requestAnimationFrame(tick);
    };

    const setRunning = () => {
      const shouldRun = !reducedMotion && onScreen && document.visibilityState === 'visible';
      if (shouldRun && !running) {
        running = true;
        raf = requestAnimationFrame(tick);
      } else if (!shouldRun && running) {
        running = false;
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
      }
    };

    const onPointerMove = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse') return;
      hasPointer = true;
      lastPointerMove = performance.now();
      const rect = canvas.getBoundingClientRect();
      target.x = e.clientX - rect.left;
      target.y = e.clientY - rect.top;
      if (mouse.x < -1000) {
        mouse.x = target.x;
        mouse.y = target.y;
      }
    };

    const onVisibility = () => setRunning();

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(canvas);
    const intersectionObserver = new IntersectionObserver(([entry]) => {
      onScreen = entry.isIntersecting;
      setRunning();
    });
    intersectionObserver.observe(canvas);

    resize();
    if (reducedMotion) {
      drawFrame(performance.now());
    } else {
      window.addEventListener('pointermove', onPointerMove, { passive: true });
      document.addEventListener('visibilitychange', onVisibility);
      setRunning();
    }

    return () => {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      intersectionObserver.disconnect();
      window.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [spacing, radius, maxDisplacement, reducedMotion]);

  return (
    <canvas ref={canvasRef} className={`pointer-events-none ${className}`} aria-hidden="true" />
  );
}
