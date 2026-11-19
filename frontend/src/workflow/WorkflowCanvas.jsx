// React Flow canvas for the generated workflow IR.
// Step 1: read-only rendering of trigger + nodes + edges (with branch handles).
// Editable nodes / live status come in a later phase.

import { Background, Controls, ReactFlow } from "@xyflow/react";
import { useMemo } from "react";

const placeholderNodes = [
  { id: "trigger", position: { x: 40, y: 80 }, data: { label: "manual_trigger" }, type: "input" },
  { id: "fetch", position: { x: 260, y: 80 }, data: { label: "http_request" }, type: "default" },
  { id: "summarize", position: { x: 500, y: 80 }, data: { label: "llm_step" }, type: "output" },
];

const placeholderEdges = [
  { id: "trigger-fetch", source: "trigger", target: "fetch" },
  { id: "fetch-summarize", source: "fetch", target: "summarize" },
];

export default function WorkflowCanvas({ workflow }) {
  const nodes = useMemo(() => {
    if (!workflow) {
      return placeholderNodes;
    }

    const triggerNode = {
      id: "trigger",
      position: { x: 40, y: 40 },
      data: { label: workflow.trigger?.type ?? "trigger" },
      type: "input",
    };

    const stepNodes = workflow.nodes.map((node, index) => ({
      id: node.id,
      position: { x: 40 + (index % 3) * 240, y: 160 + Math.floor(index / 3) * 140 },
      data: {
        label: node.max_iterations
          ? `${node.id} · ${node.type} (×${node.max_iterations})`
          : `${node.id} · ${node.type}`,
      },
      type: "default",
    }));

    return [triggerNode, ...stepNodes];
  }, [workflow]);

  const edges = useMemo(() => {
    if (!workflow) {
      return placeholderEdges;
    }

    return workflow.edges.map((edge, index) => ({
      id: `${edge.source}-${edge.target}-${index}`,
      source: edge.source,
      target: edge.target,
      label: edge.source_handle && edge.source_handle !== "main" ? edge.source_handle : undefined,
      animated: edge.source_handle === "true",
    }));
  }, [workflow]);

  return (
    <section className="flex min-h-[520px] flex-col rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-200 px-5 py-4">
        <h2 className="text-sm font-semibold text-ink">
          Workflow Graph{workflow?.goal ? ` — ${workflow.goal}` : ""}
        </h2>
      </div>
      <div className="h-[460px]">
        <ReactFlow fitView nodes={nodes} edges={edges}>
          <Background />
          <Controls />
        </ReactFlow>
      </div>
    </section>
  );
}
