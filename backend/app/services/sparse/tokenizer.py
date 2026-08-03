import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "of", "to", "in",
    "on", "for", "with", "as", "by", "at", "from", "is", "are", "was", "were",
    "be", "been", "being", "this", "that", "these", "those", "it", "its", "into",
    "about", "than", "so", "such", "not", "no", "do", "does", "did", "have",
    "has", "had", "will", "would", "can", "could", "should", "may", "might",
    "must", "shall", "i", "you", "he", "she", "we", "they", "them", "his",
    "her", "their", "our", "your", "my", "me", "us", "him", "which", "who",
    "whom", "what", "when", "where", "why", "how", "all", "any", "both",
    "each", "few", "more", "most", "other", "some", "own", "same", "just",
})


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1 and t not in _STOPWORDS]


def term_frequencies(text: str) -> dict[str, int]:
    return dict(Counter(tokenize(text)))


def top_keywords(freqs: dict[str, int], max_keywords: int) -> list[str]:
    ranked = sorted(freqs.items(), key=lambda kv: (-kv[1], kv[0]))
    return [term for term, _ in ranked[:max_keywords]]
