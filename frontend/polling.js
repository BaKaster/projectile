export const ACTIVE_STATUSES = new Set(["queued", "extracting", "analyzing"]);
export const SUCCESS_STATUSES = new Set(["ready", "requires_input"]);
export const TERMINAL_STATUSES = new Set(["ready", "requires_input", "failed"]);

export async function pollAnalysis({
  fetchRun,
  onUpdate,
  intervalMs = 4000,
  signal,
  wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}) {
  while (!signal?.aborted) {
    const run = await fetchRun(signal);
    onUpdate(run);
    if (TERMINAL_STATUSES.has(run.status)) return run;
    await wait(intervalMs);
  }
  return null;
}
