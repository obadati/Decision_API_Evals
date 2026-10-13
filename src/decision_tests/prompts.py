from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from .models import Choice, DatasetItem, Prompt


class PromptError(ValueError):
    """A prompt file is malformed or uses an unsupported schema."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, MappingNode):
            return super().construct_mapping(node, deep=deep)

        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable mapping key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _fail(source: Path, detail: str) -> PromptError:
    return PromptError(f"{source}: {detail}")


def _required_text(value: Any, field: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(source, f"'{field}' must be a non-empty string")
    return value.strip()


def _parse_choices(value: Any, source: Path) -> tuple[Choice, ...]:
    if not isinstance(value, list):
        raise _fail(source, "'choices' must be a list")
    if len(value) < 2:
        raise _fail(source, "'choices' must contain at least two choices")

    choices: list[Choice] = []
    values: set[str] = set()
    for index, entry in enumerate(value, start=1):
        if isinstance(entry, str):
            choice_value = _required_text(entry, f"choices[{index}]", source)
            description = choice_value
        elif isinstance(entry, dict):
            choice_value = _required_text(entry.get("value"), f"choices[{index}].value", source)
            raw_description = entry.get("description", choice_value)
            description = _required_text(raw_description, f"choices[{index}].description", source)
        else:
            raise _fail(
                source,
                f"'choices' entry {index} must be a string or a mapping with 'value'",
            )

        if choice_value in values:
            raise _fail(source, f"duplicate choice value {choice_value!r}")
        values.add(choice_value)
        choices.append(Choice(value=choice_value, description=description))

    return tuple(choices)


def _parse_dataset_row(row: Any, source: Path, index: int) -> DatasetItem:
    if not isinstance(row, str):
        raise _fail(source, f"dataset row {index} must be a string")
    if "|" not in row:
        raise _fail(source, f"dataset row {index} must use 'query | expected' format")
    query, expected = row.rsplit("|", 1)
    query = query.strip()
    expected = expected.strip()
    if not query or not expected:
        raise _fail(source, f"dataset row {index} has an empty query or expected value")
    return DatasetItem(query=query, expected=expected)


def _parse_dataset(value: Any, source: Path) -> tuple[DatasetItem, ...]:
    rows: list[DatasetItem]
    if isinstance(value, str):
        lines = value.splitlines()
        # YAML literal block scalars commonly have one or more trailing newlines.
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            raise _fail(source, "'dataset' must contain at least one row")
        rows = []
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                raise _fail(source, f"dataset row {index} is empty")
            rows.append(_parse_dataset_row(line, source, index))
    elif isinstance(value, list):
        if not value:
            raise _fail(source, "'dataset' must contain at least one row")
        rows = []
        for index, entry in enumerate(value, start=1):
            if isinstance(entry, dict):
                query = _required_text(entry.get("query"), f"dataset[{index}].query", source)
                expected = _required_text(
                    entry.get("expected"), f"dataset[{index}].expected", source
                )
                rows.append(DatasetItem(query=query, expected=expected))
            else:
                rows.append(_parse_dataset_row(entry, source, index))
    else:
        raise _fail(
            source,
            "'dataset' must be a list of rows or a multiline 'query | expected' string",
        )
    return tuple(rows)


def _parse_prompt(document: Any, source: Path) -> Prompt:
    if not isinstance(document, dict):
        raise _fail(source, "the YAML document must be a mapping")

    name = _required_text(document.get("name"), "name", source)
    prompt_type = _required_text(document.get("type"), "type", source)
    if prompt_type != "choice":
        raise _fail(source, f"unsupported prompt type {prompt_type!r}; expected 'choice'")
    prompt_text = _required_text(document.get("prompt"), "prompt", source)
    choices = _parse_choices(document.get("choices"), source)
    dataset = _parse_dataset(document.get("dataset"), source)

    choice_values = {choice.value for choice in choices}
    for index, item in enumerate(dataset, start=1):
        if item.expected not in choice_values:
            raise _fail(
                source,
                f"dataset row {index} expects {item.expected!r}, which is not a choice value",
            )

    return Prompt(
        name=name,
        type=prompt_type,
        prompt=prompt_text,
        choices=choices,
        dataset=dataset,
        source=source,
    )


def validate_prompt(document: Any, source: Path) -> Prompt:
    """Validate an in-memory editor document using the same rules as YAML files."""
    return _parse_prompt(document, source)


def load_prompts(directory: Path) -> list[Prompt]:
    """Load and validate all YAML prompt files in ``directory``.

    Prompt names come from each YAML document, not from its filename.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise PromptError(f"{directory}: prompt directory does not exist or is not a directory")

    paths = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
        ),
        key=lambda path: path.name,
    )
    prompts: list[Prompt] = []
    names: dict[str, Path] = {}
    for source in paths:
        try:
            with source.open(encoding="utf-8") as stream:
                document = yaml.load(stream, Loader=_UniqueKeyLoader)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            detail = getattr(exc, "problem", None) or type(exc).__name__
            mark = getattr(exc, "problem_mark", None)
            if mark is not None:
                detail = f"{detail} at line {mark.line + 1}, column {mark.column + 1}"
            raise _fail(source, f"could not parse YAML ({detail})") from exc

        prompt = _parse_prompt(document, source)
        if prompt.name in names:
            raise _fail(
                source,
                f"duplicate prompt name {prompt.name!r} also used by {names[prompt.name]}",
            )
        names[prompt.name] = source
        prompts.append(prompt)

    return prompts
