const DEFAULT_API_BASE = "http://localhost:8000";

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function normalizeApiBase(value) {
  return (value || DEFAULT_API_BASE).replace(/\/+$/, "");
}

export function formatApiError(error) {
  if (error instanceof ApiError) {
    if (typeof error.detail === "string") return error.detail;
    if (error.detail?.message) return error.detail.message;
    if (Array.isArray(error.detail)) {
      return error.detail.map((item) => item.msg || String(item)).join("; ");
    }
    return error.message;
  }
  if (error instanceof TypeError) {
    return "Не удалось связаться с сервером. Проверьте, что backend запущен.";
  }
  return error instanceof Error ? error.message : "Неизвестная ошибка";
}

async function request(apiBase, path, options = {}) {
  const response = await fetch(`${normalizeApiBase(apiBase)}${path}`, options);
  if (response.ok) return response.json();

  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  const detail = payload?.detail ?? payload;
  throw new ApiError(
    typeof detail === "string" ? detail : `Сервер вернул ошибку ${response.status}`,
    response.status,
    detail,
  );
}

export function createProject(apiBase, name) {
  return request(apiBase, "/api/v1/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export function getHealth(apiBase, signal) {
  return request(apiBase, "/health", { signal });
}

export function buildUploadFormData(files) {
  const form = new FormData();
  for (const file of files) form.append("files", file);

  const hasCompletePaths =
    files.length > 0 && files.every((file) => Boolean(file.webkitRelativePath));
  if (hasCompletePaths) {
    for (const file of files) form.append("relative_paths", file.webkitRelativePath);
  }
  return form;
}

export function uploadDocuments(apiBase, projectId, files, idempotencyKey) {
  return request(apiBase, `/api/v1/projects/${projectId}/documents`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: buildUploadFormData(files),
  });
}

export function startAnalysis(apiBase, projectId) {
  return request(apiBase, `/api/v1/projects/${projectId}/analysis-runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
}

export function getAnalysisRun(apiBase, projectId, runId, signal) {
  return request(
    apiBase,
    `/api/v1/projects/${projectId}/analysis-runs/${runId}`,
    { signal },
  );
}

export function getLatestAnalysis(apiBase, projectId, signal) {
  return request(apiBase, `/api/v1/projects/${projectId}/analyses/latest`, { signal });
}
