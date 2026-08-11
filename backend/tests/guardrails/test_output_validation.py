from app.services.guardrails.citation_rail import check_citations, confidence_score


def test_no_sources_means_nothing_to_cite():
    step = check_citations("Hello there.", [])
    assert step.action == "pass"
    assert "nothing to cite" in step.detail.lower()


def test_cited_reply_is_recognized():
    step = check_citations("The machine failed due to overheating [1].", [{"score": 0.9}])
    assert step.action == "pass"
    assert "cites" in step.detail.lower()


def test_uncited_reply_with_sources_is_flagged_but_never_blocked():
    step = check_citations("The machine failed due to overheating.", [{"score": 0.9}])
    assert step.action == "pass"  # flags via detail text, never blocks the response
    assert "no citation" in step.detail.lower()


def test_confidence_high_for_strong_relevance_scores():
    assert confidence_score([{"score": 0.9}, {"score": 0.8}]) == "high"


def test_confidence_medium_for_moderate_relevance_scores():
    assert confidence_score([{"score": 0.5}]) == "medium"


def test_confidence_low_for_weak_relevance_scores():
    assert confidence_score([{"score": 0.1}]) == "low"


def test_confidence_na_when_no_sources_were_used():
    assert confidence_score([]) == "n/a"
