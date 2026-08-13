from app.llm import LLMCreds, resolve_gemini_model


def test_resolve_gemini_model_remaps_retired_flash():
    assert resolve_gemini_model("gemini-2.0-flash") == "gemini-2.5-flash"
    assert resolve_gemini_model("models/gemini-2.0-flash") == "gemini-2.5-flash"
    assert resolve_gemini_model("gemini-2.5-flash") == "gemini-2.5-flash"


def test_llm_creds_remap_retired_gemini():
    c = LLMCreds(
        provider="gemini",
        api_key="test",
        kind="gemini",
        model="gemini-2.0-flash",
        gemini_model="gemini-2.0-flash",
    )
    assert c.model == "gemini-2.5-flash"
    assert c.gemini_model == "gemini-2.5-flash"
