// TypeScript declarations for the workflow IR (mirrors backend/app/schemas/workflow.py).

export type WorkflowStatus = "pending" | "running" | "completed" | "failed";

export interface UserGoal {
  goal: string;
}

export interface TriggerConfig {
  type: string;
  params: Record<string, unknown>;
}

export interface BindingRef {
  node: string;
  path: string;
}

export interface WorkflowNode {
  id: string;
  type: string;
  params: Record<string, unknown>;
  bindings: Record<string, BindingRef>;
  max_iterations?: number | null;
}

export interface WorkflowEdge {
  source: string;
  target: string;
  source_handle: string;
  condition?: string | null;
}

export interface Workflow {
  id: string;
  goal?: string | null;
  trigger: TriggerConfig;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export interface ValidationIssue {
  code: string;
  message: string;
  location?: string | null;
}

export interface ValidationReport {
  ok: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export interface ClarificationQuestion {
  id: string;
  question: string;
  why?: string | null;
}

export interface PlanResult {
  status: "needs_input" | "planned";
  goal: string;
  backend: string;
  questions: ClarificationQuestion[];
  workflow: Workflow | null;
  validation: ValidationReport | null;
  attempts: number;
  notes: string[];
}

export interface ExecutionLog {
  node_id: string;
  message: string;
}

export interface ExecutionResponse {
  status: "completed" | "failed";
  logs: ExecutionLog[];
  results: Record<string, unknown>;
}
