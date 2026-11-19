// Main MVP screen for generating and executing workflows.

import GoalInput from "../components/GoalInput.jsx";
import ExecutionLogsPanel from "../components/ExecutionLogsPanel.jsx";
import WorkflowCanvas from "../workflow/WorkflowCanvas.jsx";
import { useWorkflowBuilder } from "../hooks/useWorkflowBuilder.js";

export default function HomePage() {
  const {
    goal,
    setGoal,
    workflow,
    execution,
    error,
    isGenerating,
    isExecuting,
    handleGenerate,
    handleExecute,
  } = useWorkflowBuilder();

  return (
    <main className="min-h-screen bg-mist">
      <section className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-5 py-8">
        <header className="flex flex-col gap-2">
          <p className="text-sm font-semibold uppercase tracking-wide text-signal">
            Reference UI
          </p>
          <h1 className="text-3xl font-semibold text-ink">
            Agentic AI Workflow Orchestrator
          </h1>
          <p className="max-w-3xl text-base text-gray-600">
            Convert a plain-language goal into an executable workflow, then run
            it and inspect the result.
          </p>
        </header>

        <section className="grid gap-6 lg:grid-cols-[420px_1fr]">
          <div className="flex flex-col gap-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            <GoalInput goal={goal} onGoalChange={setGoal} />

            <div className="flex flex-wrap gap-3">
              <button
                className="rounded-md bg-signal px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-400"
                disabled={isGenerating || !goal.trim()}
                onClick={handleGenerate}
                type="button"
              >
                {isGenerating ? "Generating..." : "Generate Workflow"}
              </button>
              <button
                className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-ink transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-400"
                disabled={isExecuting || !workflow}
                onClick={handleExecute}
                type="button"
              >
                {isExecuting ? "Executing..." : "Execute Workflow"}
              </button>
            </div>

            {error ? (
              <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {error}
              </div>
            ) : null}

            <ExecutionLogsPanel execution={execution} />
          </div>

          <WorkflowCanvas workflow={workflow} />
        </section>
      </section>
    </main>
  );
}
