// Controlled input for the user's natural language goal.

export default function GoalInput({ goal, onGoalChange }) {
  return (
    <label className="flex flex-col gap-2">
      <span className="text-sm font-semibold text-ink">Goal Input Box</span>
      <textarea
        className="min-h-32 resize-y rounded-md border border-gray-300 bg-white px-3 py-2 text-sm leading-6 text-ink outline-none transition focus:border-signal focus:ring-2 focus:ring-blue-100"
        onChange={(event) => onGoalChange(event.target.value)}
        placeholder="Describe the outcome you want..."
        value={goal}
      />
    </label>
  );
}
