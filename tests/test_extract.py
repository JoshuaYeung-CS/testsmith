import pytest

from testsmith.extract import ExtractionError, extract_json, extract_python, strip_reasoning


def test_strip_reasoning_removes_think_blocks():
    assert strip_reasoning("<think>secret plan</think>\nanswer") == "answer"


def test_strip_reasoning_handles_missing_open_tag():
    assert strip_reasoning("reasoning without opener</think>\nanswer") == "answer"


def test_strip_reasoning_drops_unterminated_think():
    assert strip_reasoning("<think>never finished") == ""


def test_extract_python_prefers_longest_valid_python_block():
    reply = "Intro\n```python\nimport os\n```\nand the file:\n```python\nimport os\n\n\ndef test_a():\n    assert os\n```\nDone."
    code = extract_python(reply)
    assert "def test_a" in code


def test_extract_python_ignores_code_inside_think():
    reply = "<think>```python\ndef test_bad(:\n```</think>\n```python\ndef test_ok():\n    assert True\n```"
    assert "test_ok" in extract_python(reply)


def test_extract_python_accepts_untagged_fence():
    assert "def test_x" in extract_python("```\ndef test_x():\n    pass\n```")


def test_extract_python_accepts_bare_code():
    assert extract_python("import pytest\n\ndef test_x():\n    assert 1\n").startswith("import pytest")


def test_extract_python_rejects_broken_code():
    with pytest.raises(ExtractionError, match="does not parse"):
        extract_python("```python\ndef test_x(:\n```")


def test_extract_python_rejects_prose():
    with pytest.raises(ExtractionError):
        extract_python("I cannot write tests for this.")


def test_extract_json_from_fenced_block():
    assert extract_json('<think>x</think>\n```json\n[{"test": "a"}]\n```') == [{"test": "a"}]


def test_extract_json_from_prose_with_trailing_comma():
    assert extract_json('Verdicts: {"test": "a", "verdict": "real_bug",} hope that helps') == {
        "test": "a",
        "verdict": "real_bug",
    }


def test_extract_json_with_braces_inside_strings():
    payload = extract_json('[{"fixed_test": "def f():\\n    return {1: [2]}"}]')
    assert payload[0]["fixed_test"].endswith("{1: [2]}")


def test_extract_json_fails_cleanly():
    with pytest.raises(ExtractionError):
        extract_json("no json here")
