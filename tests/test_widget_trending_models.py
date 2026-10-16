"""trending_models (AI) on the recorded Hugging Face response."""
import topics
from widget_fakes import FakeResponse, ctx, fixture_json
from widgets import registry

W = registry.get("trending_models")


def test_fixture_render():
    data = W.produce(ctx({"huggingface.co/api/models": FakeResponse(200, fixture_json("hf_trending.json"))}), {})
    assert data["rows"][0] == {"id": "google/embeddinggemma-2", "task": "Feature extraction", "likes": 2494}
    assert len(data["rows"]) == 5
    html = W.render(data, {}, None)
    assert 'href="https://huggingface.co/google/embeddinggemma-2"' in html and "2,494" in html


def test_bad_ids_and_private_models_are_skipped():
    models = [{"id": "../../evil"}, {"id": "a/b", "private": True}, {"id": "javascript:x/y"}, "x",
              {"id": "ok-org/ok.model_1", "pipeline_tag": None, "likes": True}]
    data = W.produce(ctx({"huggingface.co": FakeResponse(200, models)}), {})
    assert data["rows"] == [{"id": "ok-org/ok.model_1", "task": "", "likes": None}]


def test_not_in_the_ai_email_but_offered_to_custom_newsletters():
    assert "trending_models" not in [w.id for w in topics.get_topic("ai").widgets if w.placement != "none"]
    assert "trending_models" in {w["id"] for w in registry.catalog(topic="ai")}
