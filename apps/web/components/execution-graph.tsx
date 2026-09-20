"use client";

import type { RunResponse, StageEventResponse } from "@astra/contracts";
import {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { runStreamUrl } from "@/lib/api";

import "@xyflow/react/dist/style.css";

/**
 * The execution graph (CLAUDE.md section 9).
 *
 * Every node state in here comes from a Server-Sent Event the backend published
 * while the stage was running. There is no timer, no scripted sequence and no
 * simulated progress: if a node is idle, no `stage_started` has arrived for it;
 * if it is running, one has and its `stage_completed` has not. A node's output
 * lines are fields of the payload that stage computed.
 *
 * That is the whole point. A graph that animated on a schedule would be a
 * fabricated feature under section 2.3, and the first architect in the room
 * would ask to see the event stream.
 */

type StageState = "idle" | "running" | "done" | "warning" | "failed";

type StageView = {
  stage: string;
  label: string;
  state: StageState;
  message: string;
  elapsedMs: number | null;
  outputs: [string, string][];
  warnings: string[];
};

const STATE_COLOUR: Record<StageState, string> = {
  idle: "var(--color-line-strong)",
  running: "var(--color-signal)",
  done: "var(--color-safe)",
  warning: "var(--color-warning)",
  failed: "var(--color-critical)",
};

const STATE_LABEL: Record<StageState, string> = {
  idle: "Idle",
  running: "Running",
  done: "Complete",
  warning: "Complete, with a warning",
  failed: "Failed",
};

/**
 * Which payload fields each stage shows, and how to phrase them.
 *
 * Only fields the stage actually computed appear here. A key that is absent
 * from a payload renders nothing rather than a zero, because a zero that was
 * never measured is indistinguishable on screen from one that was.
 */
const OUTPUTS: Record<string, [string, (payload: Record<string, unknown>) => string | null][]> =
  {
    INGEST: [
      ["Observations", (p) => count(p.events_in_batch)],
      ["In the log", (p) => count(p.events_total)],
      ["Roads closed", (p) => listLength(p.closed_segments)],
    ],
    HAZARD: [
      ["Cells re-scored", (p) => count(p.cells_rescored)],
      ["of grid", (p) => count(p.cells_in_grid)],
      ["Cells that moved", (p) => count(p.cells_changed)],
      ["Largest move", (p) => decimal(p.composite_delta_max, 1, " pts")],
      ["Re-classified", (p) => count(p.reclassified_cells)],
      ["Zones", (p) => count(p.zones_after)],
    ],
    EXPOSURE_VULNERABILITY: [
      ["Habitations", (p) => count(p.habitations)],
      ["Immediate tier", (p) => count(p.immediate)],
      ["Residents, immediate", (p) => count(p.immediate_population)],
      ["Hazard moved at", (p) => listLength(p.habitations_moved)],
      ["Tier changes", (p) => listLength(p.tier_changes)],
    ],
    SITE_CAPACITY: [
      ["Sites passing gates", (p) => count(p.suitable_sites)],
      ["of candidates", (p) => count(p.sites)],
      ["Effective places", (p) => decimal(p.effective_capacity, 0)],
    ],
    ROUTE_RELIABILITY: [
      ["Pairs above threshold", (p) => count(p.feasible_pairs)],
      ["of pairs", (p) => count(p.pairs)],
      ["Segments", (p) => count(p.segments)],
      ["Closed", (p) => listLength(p.closed_segments)],
    ],
    OPTIMISATION: [
      ["Solver", (p) => text(p.solver_status)],
      ["Solve time", (p) => decimal(p.solve_ms, 0, " ms")],
      ["Residents placed", (p) => count(p.population_assigned)],
      ["Unplaced", (p) => count(p.population_unmet)],
      ["Objective", (p) => decimal(p.objective_value, 0)],
    ],
    DECISION_BRIEF: [
      ["Plan requires review", (p) => bool(p.plan_requires_review)],
      ["Movements affected", (p) => count(p.invalidated_movements)],
      ["Residents affected", (p) => count(p.people_affected)],
    ],
  };

function count(value: unknown): string | null {
  return typeof value === "number" ? value.toLocaleString("en-IN") : null;
}

function decimal(value: unknown, digits: number, suffix = ""): string | null {
  return typeof value === "number"
    ? `${value.toLocaleString("en-IN", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })}${suffix}`
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function bool(value: unknown): string | null {
  return typeof value === "boolean" ? (value ? "Yes" : "No") : null;
}

function listLength(value: unknown): string | null {
  return Array.isArray(value) ? value.length.toLocaleString("en-IN") : null;
}

/** Fold the stage events a run has emitted into one view per stage. */
export function foldStages(run: {
  stage_order: string[];
  stage_labels: Record<string, string>;
  stages: StageEventResponse[];
}): StageView[] {
  const views = new Map<string, StageView>();
  for (const stage of run.stage_order) {
    views.set(stage, {
      stage,
      label: run.stage_labels[stage] ?? stage,
      state: "idle",
      message: "",
      elapsedMs: null,
      outputs: [],
      warnings: [],
    });
  }
  for (const event of run.stages) {
    const view = views.get(event.stage);
    if (!view) continue;
    if (event.status === "STARTED") {
      if (view.state === "idle") view.state = "running";
      view.message = event.message;
    } else if (event.status === "PROGRESS") {
      view.message = event.message;
    } else if (event.status === "WARNING") {
      view.state = view.state === "failed" ? "failed" : "warning";
      view.warnings.push(event.message);
    } else if (event.status === "COMPLETED") {
      view.state = view.state === "warning" ? "warning" : "done";
      view.message = event.message;
      view.elapsedMs = event.elapsed_ms;
      const payload = event.payload as Record<string, unknown>;
      view.outputs = (OUTPUTS[event.stage] ?? [])
        .map(([label, read]) => [label, read(payload)] as [string, string | null])
        .filter((entry): entry is [string, string] => entry[1] !== null);
    } else if (event.status === "FAILED") {
      view.state = "failed";
      view.message = event.message;
      view.elapsedMs = event.elapsed_ms;
    }
  }
  return run.stage_order.map((stage) => views.get(stage)!).filter(Boolean);
}

function StageNode({ data }: NodeProps) {
  const view = data as unknown as StageView;
  const colour = STATE_COLOUR[view.state];
  return (
    <div
      data-testid={`stage-node-${view.stage}`}
      data-state={view.state}
      className="w-[224px] rounded-sm border bg-[var(--color-surface)] px-2.5 py-2"
      style={{
        borderColor: colour,
        boxShadow:
          view.state === "running"
            ? `0 0 0 1px ${colour}, 0 0 18px -6px ${colour}`
            : undefined,
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0.35 }} />
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-medium text-[var(--color-ink)]">
          {view.label}
        </span>
        <span className="numeric text-[9px]" style={{ color: colour }}>
          {view.elapsedMs !== null
            ? `${view.elapsedMs.toLocaleString("en-IN", {
                maximumFractionDigits: 0,
              })} ms`
            : STATE_LABEL[view.state]}
        </span>
      </div>
      {view.outputs.length > 0 ? (
        <dl className="mt-1.5 grid grid-cols-[1fr_auto] gap-x-2 gap-y-0.5 text-[9px]">
          {view.outputs.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-[var(--color-ink-faint)]">{label}</dt>
              <dd className="numeric text-right text-[var(--color-ink-muted)]">
                {value}
              </dd>
            </div>
          ))}
        </dl>
      ) : view.message ? (
        <p className="mt-1 text-[9px] leading-relaxed text-[var(--color-ink-faint)]">
          {view.message.slice(0, 120)}
        </p>
      ) : null}
      {view.warnings.map((warning) => (
        <p
          key={warning}
          className="mt-1 border-l-2 pl-1.5 text-[9px] leading-relaxed"
          style={{ borderColor: STATE_COLOUR.warning, color: "var(--color-ink)" }}
        >
          {warning}
        </p>
      ))}
      <Handle type="source" position={Position.Right} style={{ opacity: 0.35 }} />
    </div>
  );
}

const NODE_TYPES = { stage: StageNode };

function Graph({ views }: { views: StageView[] }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  useEffect(() => {
    setNodes(
      views.map((view, index) => ({
        id: view.stage,
        type: "stage",
        position: { x: (index % 4) * 258, y: Math.floor(index / 4) * 176 },
        data: view as unknown as Record<string, unknown>,
        draggable: false,
        selectable: false,
      })),
    );
    setEdges(
      views.slice(1).map((view, index) => {
        const from = views[index];
        return {
          id: `${from.stage}-${view.stage}`,
          source: from.stage,
          target: view.stage,
          animated: view.state === "running",
          style: {
            stroke:
              from.state === "idle"
                ? "var(--color-line-strong)"
                : STATE_COLOUR[from.state],
            strokeWidth: 1.4,
          },
        };
      }),
    );
  }, [views, setNodes, setEdges]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      nodeTypes={NODE_TYPES}
      fitView
      fitViewOptions={{ padding: 0.12 }}
      proOptions={{ hideAttribution: true }}
      nodesDraggable={false}
      nodesConnectable={false}
      panOnScroll
      minZoom={0.4}
      maxZoom={1.5}
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#1d2532" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

/**
 * Subscribe to a run's SSE stream and render the stages as they execute.
 *
 * The `run` prop is the run as the POST returned it; every subsequent state
 * change comes from the stream. When the stream ends, `onFinished` fires with
 * the terminal status so the page can refresh the state the run produced.
 */
export function ExecutionGraph({
  run,
  onFinished,
}: {
  run: RunResponse | null;
  onFinished?: (runId: string, status: string) => void;
}) {
  const [stages, setStages] = useState<StageEventResponse[]>([]);
  const [connected, setConnected] = useState(false);
  const [frames, setFrames] = useState(0);
  const finishedRef = useRef<string | null>(null);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  const runId = run?.id ?? null;

  useEffect(() => {
    setStages([]);
    setFrames(0);
    if (!runId) return;
    const source = new EventSource(runStreamUrl(runId));
    const append = (raw: MessageEvent) => {
      const event = JSON.parse(raw.data) as StageEventResponse;
      setFrames((current) => current + 1);
      setStages((current) =>
        current.some((entry) => entry.sequence === event.sequence)
          ? current
          : [...current, event],
      );
    };
    for (const name of [
      "stage_started",
      "stage_progress",
      "stage_completed",
      "warning",
      "stage_failed",
    ]) {
      source.addEventListener(name, append as EventListener);
    }
    source.addEventListener("run_completed", ((raw: MessageEvent) => {
      const data = JSON.parse(raw.data) as { run_id: string; status: string };
      setFrames((current) => current + 1);
      source.close();
      setConnected(false);
      if (finishedRef.current !== data.run_id) {
        finishedRef.current = data.run_id;
        onFinishedRef.current?.(data.run_id, data.status);
      }
    }) as EventListener);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    return () => {
      source.close();
      setConnected(false);
    };
  }, [runId]);

  const views = useMemo(() => {
    if (!run) return [];
    return foldStages({
      stage_order: run.stage_order,
      stage_labels: run.stage_labels,
      stages,
    });
  }, [run, stages]);

  const reset = useCallback(() => setStages([]), []);
  void reset;

  if (!run) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center">
        <p className="max-w-md text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
          The execution graph draws itself from the pipeline&apos;s own event
          stream. Nothing is shown until a run emits something, because a node
          lighting up here has to mean a stage actually started.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col" data-testid="execution-graph">
      <div className="flex items-center gap-3 border-b border-[var(--color-line)] px-3 py-1.5">
        <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
          Execution pipeline
        </span>
        <span className="numeric text-[10px] text-[var(--color-ink-muted)]">
          {run.id}
        </span>
        <span
          className="flex items-center gap-1.5 text-[10px]"
          data-testid="stream-status"
          data-frames={frames}
        >
          <span
            aria-hidden
            className="h-1.5 w-1.5 rounded-full"
            style={{
              backgroundColor: connected
                ? "var(--color-signal)"
                : "var(--color-line-strong)",
            }}
          />
          <span className="text-[var(--color-ink-faint)]">
            {connected ? "Streaming" : "Stream closed"} ·{" "}
            <span className="numeric">{frames}</span> event
            {frames === 1 ? "" : "s"} received
          </span>
        </span>
      </div>
      <div className="min-h-0 flex-1">
        <ReactFlowProvider>
          <Graph views={views} />
        </ReactFlowProvider>
      </div>
    </div>
  );
}
