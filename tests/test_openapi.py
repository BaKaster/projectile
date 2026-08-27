from app.main import app


def test_document_upload_is_rendered_as_file_picker() -> None:
    schema = app.openapi()
    request_schema = schema["paths"][
        "/api/v1/projects/{project_id}/documents"
    ]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    component_name = request_schema["$ref"].rsplit("/", 1)[-1]
    file_items = schema["components"]["schemas"][component_name]["properties"][
        "files"
    ]["items"]

    assert file_items == {"type": "string", "format": "binary"}
    response_schema = schema["components"]["schemas"]["DocumentUploadResponse"]
    assert "upload_run_id" in response_schema["properties"]
    assert "run_id" not in response_schema["properties"]


def test_chat_and_question_endpoints_are_exposed() -> None:
    paths = app.openapi()["paths"]

    assert "get" in paths["/api/v1/chats"]
    assert "post" in paths["/api/v1/chats"]
    assert "get" in paths["/api/v1/chats/{chat_id}"]
    assert "patch" in paths["/api/v1/chats/{chat_id}"]
    assert "delete" in paths["/api/v1/chats/{chat_id}"]
    assert "post" in paths["/api/v1/chats/{chat_id}/messages"]
    assert "post" in paths[
        "/api/v1/projects/{project_id}/analysis-runs/{run_id}/answers"
    ]
    assert "post" in paths[
        "/api/v1/projects/{project_id}/analysis-runs/{run_id}/questions/skip"
    ]
    assert "get" in paths["/api/v1/project-types"]
    assert "patch" in paths[
        "/api/v1/projects/{project_id}/analysis-runs/{run_id}/project-type"
    ]
    pdf = paths[
        "/api/v1/projects/{project_id}/analysis-runs/{run_id}/report.pdf"
    ]["get"]
    assert "application/pdf" in pdf["responses"]["200"]["content"]
    xlsx = paths[
        "/api/v1/projects/{project_id}/analysis-runs/{run_id}/report.xlsx"
    ]["get"]
    assert (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        in xlsx["responses"]["200"]["content"]
    )
    proposal = paths[
        "/api/v1/projects/{project_id}/analysis-runs/{run_id}/proposal.docx"
    ]["get"]
    assert (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        in proposal["responses"]["200"]["content"]
    )


def test_stage_plan_endpoint_has_typed_contract() -> None:
    schema = app.openapi()
    endpoint = schema["paths"][
        "/api/v1/project-types/{project_type_code}/stage-plan"
    ]["post"]
    response = endpoint["responses"]["200"]["content"]["application/json"][
        "schema"
    ]

    assert response["$ref"].endswith("/ProjectStagePlan")
    stage_schema = schema["components"]["schemas"]["ResolvedStage"]
    assert {"status", "exit_gate", "work_generation"} <= set(
        stage_schema["properties"]
    )


def test_work_plan_endpoint_has_typed_contract() -> None:
    schema = app.openapi()
    endpoint = schema["paths"][
        "/api/v1/project-types/{project_type_code}/work-plan"
    ]["post"]
    response = endpoint["responses"]["200"]["content"]["application/json"][
        "schema"
    ]

    assert response["$ref"].endswith("/GeneratedWorkPlan")
    work_schema = schema["components"]["schemas"]["WorkItem"]
    assert {"selection_reason", "outputs", "estimation_drivers"} <= set(
        work_schema["properties"]
    )
