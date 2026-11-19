// State hook that coordinates workflow generation and execution.

import { useState } from "react";
import { executeWorkflow, generateWorkflow } from "../services/api.js";

export function useWorkflowBuilder() {
  const [goal, setGoal] = useState("Research AI agents and create report");
  const [workflow, setWorkflow] = useState(null);
  const [execution, setExecution] = useState(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);
  const [error, setError] = useState("");

  async function handleGenerate() {
    setError("");
    setExecution(null);
    setIsGenerating(true);

    try {
      const nextWorkflow = await generateWorkflow(goal);
      setWorkflow(nextWorkflow);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setIsGenerating(false);
    }
  }

  async function handleExecute() {
    if (!workflow) {
      setError("Generate a workflow before executing it.");
      return;
    }

    setError("");
    setIsExecuting(true);

    try {
      const executionResult = await executeWorkflow(workflow);
      setExecution(executionResult);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setIsExecuting(false);
    }
  }

  return {
    goal,
    setGoal,
    workflow,
    execution,
    error,
    isGenerating,
    isExecuting,
    handleGenerate,
    handleExecute,
  };
}
