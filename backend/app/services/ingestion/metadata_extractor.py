def detect_language(text: str) -> str | None:
    if not text or len(text.strip()) < 20:
        return None
    try:
        from langdetect import detect

        return detect(text)
    except Exception:
        return None


def extract_keywords(text: str, language: str | None, max_keywords: int = 10) -> list[str]:
    if not text or not text.strip():
        return []
    try:
        import yake

        extractor = yake.KeywordExtractor(lan=language or "en", n=2, top=max_keywords)
        return [phrase for phrase, _score in extractor.extract_keywords(text)]
    except Exception:
        return []
