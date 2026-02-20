"""Shared test helpers — mock Gemini response factories."""

from unittest.mock import MagicMock


def _make_text_response(text: str):
    """Create a mock Gemini response with text content."""
    part = MagicMock()
    part.text = text
    part.function_call = None

    content = MagicMock()
    content.role = "model"
    content.parts = [part]

    candidate = MagicMock()
    candidate.content = content

    response = MagicMock()
    response.candidates = [candidate]
    response.function_calls = None
    response.text = text
    return response


def _make_fn_call_response(name: str, args: dict):
    """Create a mock Gemini response with a function call."""
    fn_call = MagicMock()
    fn_call.name = name
    fn_call.args = args

    fn_part = MagicMock()
    fn_part.function_call = fn_call

    content = MagicMock()
    content.role = "model"
    content.parts = [fn_part]

    candidate = MagicMock()
    candidate.content = content

    response = MagicMock()
    response.candidates = [candidate]
    response.function_calls = [fn_call]
    response.text = None
    return response
