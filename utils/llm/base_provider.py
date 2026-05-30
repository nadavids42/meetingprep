from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Provider-agnostic interface for text generation."""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Return a model response for the supplied prompt."""
        raise NotImplementedError
