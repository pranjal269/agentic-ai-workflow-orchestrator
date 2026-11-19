// Execution log panel for workflow run feedback.

export default function ExecutionLogsPanel({ execution }) {
  const logs = execution?.logs ?? [];

  return (
    <section className="flex min-h-48 flex-col gap-3 rounded-md border border-gray-200 bg-gray-50 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink">Execution Logs Panel</h2>
        <span className="rounded-full bg-white px-2 py-1 text-xs font-medium text-gray-600">
          {execution?.status ?? "idle"}
        </span>
      </div>

      {logs.length > 0 ? (
        <ol className="space-y-2">
          {logs.map((log, index) => (
            <li
              className="rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700"
              key={`${log.node_id}-${index}`}
            >
              <span className="font-medium text-ink">{log.node_id}</span>
              <span className="block">{log.message}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-sm text-gray-500">
          Workflow execution logs will appear here.
        </p>
      )}
    </section>
  );
}
