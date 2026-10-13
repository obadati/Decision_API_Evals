from __future__ import annotations

from pathlib import Path

import pytest

from decision_tests.models import Choice, DatasetItem
from decision_tests.prompts import PromptError, load_prompts


def write_prompt(directory: Path, filename: str, content: str) -> Path:
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_names_and_all_supported_choice_and_dataset_shapes(tmp_path: Path) -> None:
    write_prompt(
        tmp_path,
        "choice-list.yaml",
        """name: YAML defined name
type: choice
prompt: Pick a label
choices:
  - value: "yes"
    description: Affirmative
  - "no"
dataset:
  - "query | with pipe | yes"
  - query: another query
    expected: "no"
""",
    )
    write_prompt(
        tmp_path,
        "block.yml",
        """name: multiline data
type: choice
prompt: Classify
choices: ["yes", "no"]
dataset: |
  first query | yes
  second query | no
""",
    )

    prompts = load_prompts(tmp_path)

    assert [prompt.name for prompt in prompts] == ["multiline data", "YAML defined name"]
    assert prompts[0].dataset == (
        DatasetItem(query="first query", expected="yes"),
        DatasetItem(query="second query", expected="no"),
    )
    assert prompts[1].source == tmp_path / "choice-list.yaml"
    assert prompts[1].choices == (
        Choice(value="yes", description="Affirmative"),
        Choice(value="no", description="no"),
    )
    assert prompts[1].dataset[0] == DatasetItem(query="query | with pipe", expected="yes")


def test_rejects_duplicate_yaml_keys_with_file_context(tmp_path: Path) -> None:
    write_prompt(
        tmp_path,
        "broken.yaml",
        """name: first
name: second
type: choice
prompt: Pick
choices: ["yes", "no"]
dataset: ["query | yes"]
""",
    )

    with pytest.raises(PromptError, match="broken.yaml.*duplicate key"):
        load_prompts(tmp_path)


def test_rejects_duplicate_prompt_names_even_when_filenames_differ(tmp_path: Path) -> None:
    contents = """name: same name
type: choice
prompt: Pick
choices: ["yes", "no"]
dataset: ["query | yes"]
"""
    write_prompt(tmp_path, "a.yaml", contents)
    write_prompt(tmp_path, "z.yml", contents)

    with pytest.raises(PromptError, match="duplicate prompt name 'same name'"):
        load_prompts(tmp_path)


@pytest.mark.parametrize(
    ("field_content", "message"),
    [
        ("type: freeform", "unsupported prompt type"),
        ('choices: ["yes", "yes"]', "duplicate choice value"),
        ('choices: ["yes"]', "at least two choices"),
        ("dataset: [missing delimiter]", "query | expected"),
        ('dataset: ["query | "]', "empty query or expected"),
        ("dataset: []", "at least one row"),
        ('dataset: ["query | perhaps"]', "not a choice value"),
    ],
)
def test_rejects_invalid_schema_fields_with_file_context(
    tmp_path: Path, field_content: str, message: str
) -> None:
    fields = {
        "type": "type: choice",
        "choices": 'choices: ["yes", "no"]',
        "dataset": 'dataset: ["query | yes"]',
    }
    field_name = field_content.split(":", 1)[0]
    fields[field_name] = field_content
    write_prompt(
        tmp_path,
        "invalid.yaml",
        "\n".join(
            [
                "name: invalid",
                fields["type"],
                "prompt: Pick",
                fields["choices"],
                fields["dataset"],
            ]
        )
        + "\n",
    )

    with pytest.raises(PromptError, match="invalid.yaml") as error:
        load_prompts(tmp_path)
    assert message in str(error.value)


def test_rejects_blank_rows_in_block_scalar(tmp_path: Path) -> None:
    write_prompt(
        tmp_path,
        "blank-row.yaml",
        """name: blank row
type: choice
prompt: Pick
choices: ["yes", "no"]
dataset: |
  query | yes

  other | no
""",
    )

    with pytest.raises(PromptError, match="dataset row 2 is empty"):
        load_prompts(tmp_path)
