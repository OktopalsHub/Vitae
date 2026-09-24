from app.observability import current_request_id

def test_request_id_context_is_empty_by_default():
    assert current_request_id() == ""
