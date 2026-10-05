import hashlib

from litestar.testing import TestClient

from manim_control import api


def test_generation_creation_is_idempotent(monkeypatch):
    monkeypatch.setattr(api, "_dispatch", lambda generation_id: None)
    headers = {"X-API-Key": "dev-secret", "Idempotency-Key": "same-request"}
    with TestClient(api.app) as client:
        first = client.post("/v1/generations", headers=headers, json={"prompt": "Explain vectors"})
        second = client.post("/v1/generations", headers=headers, json={"prompt": "Explain vectors"})
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]


def test_idempotency_key_cannot_hide_a_different_request(monkeypatch):
    monkeypatch.setattr(api, "_dispatch", lambda generation_id: None)
    headers = {"X-API-Key": "dev-secret", "Idempotency-Key": "collision"}
    with TestClient(api.app) as client:
        assert client.post("/v1/generations", headers=headers, json={"prompt": "First prompt"}).status_code == 202
        response = client.post("/v1/generations", headers=headers, json={"prompt": "Different prompt"})
    assert response.status_code == 409


def test_api_key_is_required(monkeypatch):
    monkeypatch.setattr(api, "_dispatch", lambda generation_id: None)
    with TestClient(api.app) as client:
        response = client.post(
            "/v1/generations",
            headers={"Idempotency-Key": "x"},
            json={"prompt": "Explain vectors"},
        )
    assert response.status_code == 422


def test_v1_docs_alias_and_validation_shape():
    with TestClient(api.litestar_app) as client:
        schema = client.get("/openapi.json")
        invalid = client.post(
            "/v1/generations",
            headers={"X-API-Key": "dev-secret", "Idempotency-Key": "invalid"},
            json={"prompt": "x", "unexpected": True},
        )
    assert schema.status_code == 200
    assert "/v1/generations" in schema.json()["paths"]
    assert invalid.status_code == 422
    assert "detail" in invalid.json()


def test_enterprise_operations_routes_are_not_exposed():
    with TestClient(api.app) as client:
        assert client.get("/metrics").status_code == 404
        assert client.get("/internal/v1/render-jobs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_asset_upload_is_hash_verified():
    payload = b"not-a-real-image-but-valid-upload-contract"
    digest = hashlib.sha256(payload).hexdigest()
    headers = {"X-API-Key": "dev-secret"}
    with TestClient(api.app) as client:
        intent = client.post(
            "/v1/assets/upload-intents",
            headers=headers,
            json={
                "filename": "my diagram.png",
                "media_type": "image/png",
                "size_bytes": len(payload),
                "sha256": digest,
            },
        )
        assert intent.status_code == 201
        uploaded = client.put(
            intent.json()["upload_url"], content=payload, headers={"Content-Type": "image/png"}
        )
        assert uploaded.status_code == 204
        completed = client.post(
            f"/v1/assets/{intent.json()['asset_id']}/complete",
            headers=headers,
            json={"sha256": digest},
        )
    assert completed.status_code == 204
