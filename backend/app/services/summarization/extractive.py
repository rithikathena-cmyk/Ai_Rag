from app.core.config import settings
from app.services.chunking.text_utils import split_sentences
from app.services.sparse.tokenizer import tokenize


def summarize(text: str) -> str | None:
    sentences = split_sentences(text)
    if not sentences:
        return None
    if len(sentences) <= settings.summary_min_sentences:
        return " ".join(sentences)

    word_freqs: dict[str, int] = {}
    for sentence in sentences:
        for term in tokenize(sentence):
            word_freqs[term] = word_freqs.get(term, 0) + 1
    if not word_freqs:
        return None
    max_freq = max(word_freqs.values())
    normalized = {term: count / max_freq for term, count in word_freqs.items()}

    scored = []
    for idx, sentence in enumerate(sentences):
        terms = tokenize(sentence)
        score = sum(normalized.get(t, 0.0) for t in terms) / len(terms) if terms else 0.0
        scored.append((idx, score, sentence))

    target = round(len(sentences) * settings.summary_target_ratio)
    target = max(settings.summary_min_sentences, min(settings.summary_max_sentences, target))
    target = min(target, len(sentences))

    top = sorted(scored, key=lambda x: (-x[1], x[0]))[:target]
    ordered = sorted(top, key=lambda x: x[0])
    return " ".join(s for _, _, s in ordered)
