// Shared JSDoc typedefs for the workflow IR (mirrors backend/app/schemas/workflow.py).

/**
 * @typedef {Object} UserGoal
 * @property {string} goal
 */

/**
 * @typedef {"pending" | "running" | "completed" | "failed"} WorkflowStatus
 */

/**
 * @typedef {Object} TriggerConfig
 * @property {string} type - node type of a "trigger" category node
 * @property {Record<string, unknown>} params
 */

/**
 * @typedef {Object} BindingRef
 * @property {string} node - source node id, or "trigger"
 * @property {string} path - dotted/bracketed accessor into that node's output
 */

/**
 * @typedef {Object} WorkflowNode
 * @property {string} id
 * @property {string} type
 * @property {Record<string, unknown>} params
 * @property {Record<string, BindingRef>} bindings
 * @property {number | null} [max_iterations]
 */

/**
 * @typedef {Object} WorkflowEdge
 * @property {string} source
 * @property {string} target
 * @property {string} source_handle - output branch of the source node (e.g. "main", "true")
 * @property {string | null} [condition]
 */

/**
 * @typedef {Object} Workflow
 * @property {string} id
 * @property {string | null} [goal]
 * @property {TriggerConfig} trigger
 * @property {WorkflowNode[]} nodes
 * @property {WorkflowEdge[]} edges
 */

/**
 * @typedef {Object} ValidationIssue
 * @property {string} code
 * @property {string} message
 * @property {string | null} [location]
 */

/**
 * @typedef {Object} ValidationReport
 * @property {boolean} ok
 * @property {ValidationIssue[]} errors
 * @property {ValidationIssue[]} warnings
 */

/**
 * @typedef {Object} ClarificationQuestion
 * @property {string} id
 * @property {string} question
 * @property {string | null} [why]
 */

/**
 * @typedef {Object} PlanResult
 * @property {"needs_input" | "planned"} status
 * @property {string} goal
 * @property {string} backend - "nvidia" | "stub"
 * @property {ClarificationQuestion[]} questions
 * @property {Workflow | null} workflow
 * @property {ValidationReport | null} validation
 * @property {number} attempts
 * @property {string[]} notes
 */

/**
 * @typedef {Object} ExecutionLog
 * @property {string} node_id
 * @property {string} message
 */

/**
 * @typedef {Object} ExecutionResponse
 * @property {"completed" | "failed"} status
 * @property {ExecutionLog[]} logs
 * @property {Record<string, unknown>} results
 * @property {string | null} [run_id]
 * @property {string | null} [started_at]
 * @property {string | null} [finished_at]
 */

export {};
