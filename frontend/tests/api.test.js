import assert from "node:assert/strict";
import test from "node:test";
import {
  ApiError,
  analysisExcelUrl,
  analysisReportUrl,
  buildUploadFormData,
  createProject,
  createChat,
  deleteChat,
  downloadAnalysisExcel,
  getProjectTypes,
  getLatestAnalysis,
  sendChatMessage,
  answerAnalysisQuestions,
  skipAnalysisQuestions,
  updateChat,
  updateAnalysisProjectType,
  uploadDocuments,
} from "../api.js";

test("analysis report URL carries the selected visual theme", () => {
  const base = "http://localhost:8000";
  assert.equal(
    analysisReportUrl(base, "project", "run", "dark"),
    `${base}/api/v1/projects/project/analysis-runs/run/report.pdf?theme=dark`,
  );
  assert.equal(
    analysisReportUrl(base, "project", "run", "unknown"),
    `${base}/api/v1/projects/project/analysis-runs/run/report.pdf?theme=light`,
  );
});

test("Excel report URL targets the completed analysis", () => {
  assert.equal(
    analysisExcelUrl("http://localhost:8000", "project", "run"),
    "http://localhost:8000/api/v1/projects/project/analysis-runs/run/report.xlsx",
  );
});

test("Excel download exposes project attachment metadata", async (context) => {
  context.mock.method(Date, "now", () => 123456789);
  const fetchMock = context.mock.method(globalThis, "fetch", async () => new Response("xlsx", {
    status: 200,
    headers: {
      "Content-Disposition": "attachment; filename=estimate.xlsx; filename*=UTF-8''project-%D0%BE%D1%86%D0%B5%D0%BD%D0%BA%D0%B0.xlsx",
      "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "X-Projectile-Artifact-Attached": "true",
      "X-Projectile-Artifact-Document-Id": "document-id",
    },
  }));

  const result = await downloadAnalysisExcel("http://localhost:8000", "project", "run");

  assert.equal(
    fetchMock.mock.calls[0].arguments[0],
    "http://localhost:8000/api/v1/projects/project/analysis-runs/run/report.xlsx?download=123456789",
  );
  assert.deepEqual(fetchMock.mock.calls[0].arguments[1], { cache: "no-store" });
  assert.equal(result.filename, "project-оценка.xlsx");
  assert.equal(result.attached, true);
  assert.equal(result.documentId, "document-id");
  assert.equal(await result.blob.text(), "xlsx");
});

function mockResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("buildUploadFormData adds repeated files without paths for a regular selection", () => {
  const files = [new File(["one"], "ТЗ.pdf"), new File(["two"], "смета.xlsx")];
  const form = buildUploadFormData(files);

  assert.deepEqual(form.getAll("files").map((file) => file.name), ["ТЗ.pdf", "смета.xlsx"]);
  assert.deepEqual(form.getAll("relative_paths"), []);
});

test("buildUploadFormData adds a matching relative path for every folder file", () => {
  const first = new File(["one"], "ТЗ.pdf");
  const second = new File(["two"], "смета.xlsx");
  Object.defineProperty(first, "webkitRelativePath", { value: "Проект/ТЗ.pdf" });
  Object.defineProperty(second, "webkitRelativePath", { value: "Проект/Финансы/смета.xlsx" });

  const form = buildUploadFormData([first, second]);
  assert.equal(form.getAll("files").length, 2);
  assert.deepEqual(form.getAll("relative_paths"), ["Проект/ТЗ.pdf", "Проект/Финансы/смета.xlsx"]);
});

test("buildUploadFormData omits all relative paths when only some files have them", () => {
  const first = new File(["one"], "ТЗ.pdf");
  const second = new File(["two"], "смета.xlsx");
  Object.defineProperty(first, "webkitRelativePath", { value: "Проект/ТЗ.pdf" });

  assert.deepEqual(buildUploadFormData([first, second]).getAll("relative_paths"), []);
});

test("API calls use the backend contract and preserve FormData boundary", async (context) => {
  const calls = [];
  context.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, options });
    return mockResponse({ id: "project-id", name: "Тест" }, options.method === "POST" ? 201 : 200);
  });

  await createProject("http://localhost:8000/", "Тест");
  await uploadDocuments("http://localhost:8000", "project-id", [new File(["x"], "a.txt")], "idem-key");

  assert.equal(calls[0].url, "http://localhost:8000/api/v1/projects");
  assert.deepEqual(JSON.parse(calls[0].options.body), { name: "Тест" });
  assert.equal(calls[1].options.headers["Idempotency-Key"], "idem-key");
  assert.equal(calls[1].options.headers["Content-Type"], undefined);
  assert.ok(calls[1].options.body instanceof FormData);
});

test("latest analysis exposes a 404 as ApiError for restoration logic", async (context) => {
  context.mock.method(globalThis, "fetch", async () => mockResponse({ detail: "Project analysis not found" }, 404));
  await assert.rejects(
    getLatestAnalysis("http://localhost:8000", "project-id"),
    (error) => error instanceof ApiError && error.status === 404,
  );
});

test("chat lifecycle, messages, answers and skip use their explicit endpoints", async (context) => {
  const calls = [];
  context.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, options });
    return mockResponse({ id: "ok" }, 200);
  });

  await createChat("http://localhost:8000", "Новый проект");
  await updateChat("http://localhost:8000", "chat-id", "Новое название");
  await sendChatMessage("http://localhost:8000", "chat-id", "Оцени проект");
  await answerAnalysisQuestions("http://localhost:8000", "chat-id", "run-id", "Срок — декабрь");
  await skipAnalysisQuestions("http://localhost:8000", "chat-id", "run-id");
  await getProjectTypes("http://localhost:8000");
  await updateAnalysisProjectType("http://localhost:8000", "chat-id", "run-id", "SEC_Audit");
  await deleteChat("http://localhost:8000", "chat-id");

  assert.equal(calls[0].url, "http://localhost:8000/api/v1/chats");
  assert.equal(calls[1].url, "http://localhost:8000/api/v1/chats/chat-id");
  assert.equal(calls[1].options.method, "PATCH");
  assert.equal(calls[2].url, "http://localhost:8000/api/v1/chats/chat-id/messages");
  assert.equal(calls[3].url, "http://localhost:8000/api/v1/projects/chat-id/analysis-runs/run-id/answers");
  assert.equal(calls[4].url, "http://localhost:8000/api/v1/projects/chat-id/analysis-runs/run-id/questions/skip");
  assert.equal(calls[5].url, "http://localhost:8000/api/v1/project-types");
  assert.equal(calls[6].url, "http://localhost:8000/api/v1/projects/chat-id/analysis-runs/run-id/project-type");
  assert.deepEqual(JSON.parse(calls[6].options.body), { project_type_code: "SEC_Audit" });
  assert.equal(calls[7].options.method, "DELETE");
});
