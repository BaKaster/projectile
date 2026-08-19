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
