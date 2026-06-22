from pathlib import Path

from src.config import load_environment, provider_status
from src.drafter import build_llm_prompt, detect_forbidden_claims, load_drafting_context
from src.llm import build_llm
from src.models import Scholarship
from src.source_adapters.search import build_search_provider


ROOT = Path(__file__).resolve().parents[1]


def test_dotenv_loading_and_no_key_fallback(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_PROVIDER=openai\nLLM_MODEL=test-model\nOPENAI_API_KEY=\n", encoding="utf-8")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    load_environment(env_file, override=True)
    assert provider_status()["llm_model"] == "test-model"
    assert build_llm().enabled is False


def test_llm_prompt_contains_safety_rules_and_scholarship_data() -> None:
    context = load_drafting_context(ROOT / "data")
    scholarship = Scholarship(name="Test Award", amount=2500, deadline="2027-01-01")
    prompt = build_llm_prompt(context, scholarship, "Describe your leadership.")
    assert "Never claim low income" in prompt
    assert "exact Mission Arlington tasks" in prompt
    assert "Test Award" in prompt and "2500" in prompt


def test_forbidden_claim_detection() -> None:
    assert detect_forbidden_claims("I completed the FAFSA and volunteered 100 service hours.")
    assert detect_forbidden_claims("I am a first-generation student.")


def test_tavily_uses_tavily_specific_key() -> None:
    provider = build_search_provider({"SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "secret"})
    assert provider.enabled and provider.name == "tavily"
