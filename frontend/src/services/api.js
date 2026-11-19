// API client for the FastAPI backend.

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return response.json();
}

/**
 * Generate a workflow from a natural language goal.
 * Always resolves to a Workflow (no clarification loop).
 *
 * @param {string} goal
 */
export function generateWorkflow(goal) {
  return request("/workflow/generate", {
    method: "POST",
    body: JSON.stringify({ goal }),
  });
}

/**
 * Plan a workflow from a goal. May return clarifying questions
 * (`status: "needs_input"`) or a validated workflow (`status: "planned"`).
 * Answered questions are sent back via `clarifications`.
 *
 * @param {string} goal
 * @param {{ question: string, answer: string }[]} [clarifications]
 * @returns {Promise<import("../types/workflow.js").PlanResult>}
 */
export function planWorkflow(goal, clarifications = []) {
  return request("/workflow/plan", {
    method: "POST",
    body: JSON.stringify({ goal, clarifications }),
  });
}

/**
 * Statically validate a workflow against the node catalog.
 *
 * @param {import("../types/workflow.js").Workflow} workflow
 * @returns {Promise<import("../types/workflow.js").ValidationReport>}
 */
export function validateWorkflow(workflow) {
  return request("/workflow/validate", {
    method: "POST",
    body: JSON.stringify(workflow),
  });
}

/**
 * Execute a generated workflow.
 *
 * @param {import("../types/workflow.js").Workflow} workflow
 */
export function executeWorkflow(workflow) {
  return request("/workflow/execute", {
    method: "POST",
    body: JSON.stringify(workflow),
  });
}
