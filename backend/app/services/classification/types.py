from dataclasses import dataclass


@dataclass
class ClassificationResult:
    label: str
    confidence: float
    method: str
