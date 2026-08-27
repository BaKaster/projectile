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
  if (response.ok) return response.status === 204 ? null : response.json();

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

export function createChat(apiBase, name) {
  return request(apiBase, "/api/v1/chats", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(name ? { name } : {}),
  });
}

export function listChats(apiBase, signal) {
  return request(apiBase, "/api/v1/chats", { signal });
}

export function getProjectTypes(apiBase, signal) {
  return request(apiBase, "/api/v1/project-types", { signal });
}

export function getChat(apiBase, chatId, signal) {
  return request(apiBase, `/api/v1/chats/${chatId}`, { signal });
}

export function updateChat(apiBase, chatId, name) {
  return request(apiBase, `/api/v1/chats/${chatId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export function deleteChat(apiBase, chatId) {
  return request(apiBase, `/api/v1/chats/${chatId}`, { method: "DELETE" });
}

export function sendChatMessage(apiBase, chatId, content) {
  return request(apiBase, `/api/v1/chats/${chatId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export function answerAnalysisQuestions(apiBase, projectId, runId, content) {
  return request(apiBase, `/api/v1/projects/${projectId}/analysis-runs/${runId}/answers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export function skipAnalysisQuestions(apiBase, projectId, runId) {
  return request(apiBase, `/api/v1/projects/${projectId}/analysis-runs/${runId}/questions/skip`, {
    method: "POST",
  });
}

export function analysisReportUrl(apiBase, projectId, runId, theme = "light") {
  const reportTheme = theme === "dark" ? "dark" : "light";
  return `${normalizeApiBase(apiBase)}/api/v1/projects/${projectId}/analysis-runs/${runId}/report.pdf?theme=${reportTheme}`;
}

export function analysisExcelUrl(apiBase, projectId, runId) {
  return `${normalizeApiBase(apiBase)}/api/v1/projects/${projectId}/analysis-runs/${runId}/report.xlsx`;
}

export function analysisProposalUrl(apiBase, projectId, runId) {
  return `${normalizeApiBase(apiBase)}/api/v1/projects/${projectId}/analysis-runs/${runId}/proposal.docx`;
}

export function updateAnalysisProjectType(apiBase, projectId, runId, projectTypeCode) {
  return request(apiBase, `/api/v1/projects/${projectId}/analysis-runs/${runId}/project-type`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_type_code: projectTypeCode }),
  });
}

export async function downloadAnalysisExcel(apiBase, projectId, runId) {
  return downloadProjectArtifact(
    `${analysisExcelUrl(apiBase, projectId, runId)}?download=${Date.now()}`,
    "projectile-estimate.xlsx",
  );
}

export async function downloadCommercialProposal(apiBase, projectId, runId) {
  return downloadProjectArtifact(
    `${analysisProposalUrl(apiBase, projectId, runId)}?download=${Date.now()}`,
    "projectile-proposal.docx",
  );
}

async function downloadProjectArtifact(url, fallbackFilename) {
  const response = await fetch(url, {
    cache: "no-store",
  });
  if (!response.ok) {
    let payload;
    try { payload = await response.json(); } catch { payload = null; }
    const detail = payload?.detail ?? payload;
    throw new ApiError(
      typeof detail === "string" ? detail : `Сервер вернул ошибку ${response.status}`,
      response.status,
      detail,
    );
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plainName = disposition.match(/filename="([^"]+)"/i)?.[1];
  let filename = plainName || fallbackFilename;
  if (encodedName) {
    try { filename = decodeURIComponent(encodedName); } catch { filename = plainName || filename; }
  }
  return {
    blob: await response.blob(),
    filename,
    attached: response.headers.get("X-Projectile-Artifact-Attached") === "true",
    documentId: response.headers.get("X-Projectile-Artifact-Document-Id"),
  };
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

export function uploadRateImport(apiBase, file, autoApply) {
  const form = new FormData(); form.append("file", file); form.append("auto_apply", String(Boolean(autoApply)));
  return request(apiBase, "/api/v1/rate-imports", { method: "POST", body: form });
}
export function applyRateImport(apiBase, importId, items) {
  return request(apiBase, `/api/v1/rate-imports/${importId}/apply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }) });
}
