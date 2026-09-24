def test_worker_operations_requires_admin(client):
    response = client.get("/admin/operations/workers", follow_redirects=False)
    assert response.status_code in {302, 303}
