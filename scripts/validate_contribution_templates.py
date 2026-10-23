#!/usr/bin/env python3
"""Validate contribution forms, PR governance gates, and affected documentation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
START_MARKER = "<!-- shared-governance-gates:start -->"
END_MARKER = "<!-- shared-governance-gates:end -->"
OLD_READY_LABEL = "status:" + "agent-ready"
APPROVAL_CONTRACT = (
    "I understand that only a human maintainer may label this proposal "
    "`status:ready` before implementation."
)

EXPECTED_GOVERNANCE_BLOCK = """<!-- shared-governance-gates:start -->
## Required governance gates

- [ ] The PR implements one approved Issue and stays within its scope.
- [ ] The linked Issue was claimed and remains `status:in-progress`.
- [ ] Tests and documentation match the changed behavior.
- [ ] No credentials, proprietary data, or unrelated changes are included.
- [ ] Commits include DCO sign-off (`git commit -s`).
- [ ] The head branch is in a contributor fork and the base is upstream `main`.
- [ ] The branch is current with upstream `main`.
- [ ] `DCO`, `Scripts and profile contracts`, and `Explorer Python` pass on the
      current head.
- [ ] All review threads will be resolved before merge.
- [ ] Review path: a non-owner PR has one current human `cadence-dev` approval, or
      this is an owner-authored PR using the recorded owner review bypass.
- [ ] Any owner review bypass use is recorded in this PR and is not being used on
      a non-owner PR or to bypass required checks or unresolved review threads.
- [ ] Human review does not itself authorize merge; I will wait for explicit
      repository-owner merge authorization.
- [ ] No coding agent approved this PR or supplied merge authority.
<!-- shared-governance-gates:end -->"""

MANDATORY_GOVERNANCE_GATES = {
    "approved-linked-issue": (
        "The PR implements one approved Issue and stays within its scope.",
        "The linked Issue was claimed and remains `status:in-progress`.",
    ),
    "dco-signoff": ("Commits include DCO sign-off (`git commit -s`).",),
    "required-ci": (
        "`DCO`, `Scripts and profile contracts`, and `Explorer Python` pass on the\n"
        "      current head.",
    ),
    "branch-currency": ("The branch is current with upstream `main`.",),
    "current-human-review": (
        "Review path: a non-owner PR has one current human `cadence-dev` approval, or\n"
        "      this is an owner-authored PR using the recorded owner review bypass.",
        "Any owner review bypass use is recorded in this PR and is not being used on\n"
        "      a non-owner PR or to bypass required checks or unresolved review threads.",
    ),
    "resolved-threads": ("All review threads will be resolved before merge.",),
    "owner-merge-authorization": (
        "Human review does not itself authorize merge; I will wait for explicit\n"
        "      repository-owner merge authorization.",
    ),
    "no-agent-authority": (
        "No coding agent approved this PR or supplied merge authority.",
    ),
}

GENERAL_FORM_FIELDS = {
    "area",
    "idea",
    "context",
    "value",
    "constraints",
    "approval",
}
HUMAN_FORM_FIELDS = {
    "area",
    "learning_value",
    "problem",
    "paths",
    "starting_commands",
    "observable_gap",
    "edit_locations",
    "scope",
    "validation_commands",
    "expected_success",
    "stopping_point",
    "handoff_requirements",
    "approval",
}
AGENT_FORM_FIELDS = {
    "area",
    "problem",
    "context",
    "scope",
    "authority",
    "specifications",
    "acceptance",
    "validation",
    "limitations",
    "blockers",
    "handoff",
    "approval",
}

DOCS = (
    "AGENTS.md",
    "WORKFLOW.md",
    "CONTRIBUTING.md",
    "agent-context/README.md",
)


class ValidationError(ValueError):
    """Raised when a contribution contract is invalid."""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_governance_block(text: str) -> str:
    if text.count(START_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise ValidationError("each PR template must contain one governance block")
    start = text.index(START_MARKER)
    end = text.index(END_MARKER, start) + len(END_MARKER)
    return text[start:end]


def validate_governance_texts(human_text: str, agent_text: str) -> None:
    human_block = extract_governance_block(human_text)
    agent_block = extract_governance_block(agent_text)
    if human_block != agent_block:
        raise ValidationError("PR governance blocks differ")
    if human_block != EXPECTED_GOVERNANCE_BLOCK:
        raise ValidationError("PR governance block changed from the mandatory contract")


def validate_pr_templates(root: Path = ROOT) -> None:
    human_path = root / ".github/PULL_REQUEST_TEMPLATE.md"
    agent_path = root / ".github/PULL_REQUEST_TEMPLATE/agent-first.md"
    if not human_path.is_file() or not agent_path.is_file():
        raise ValidationError("both PR template paths must exist")
    validate_governance_texts(_read(human_path), _read(agent_path))


def _load_form(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(_read(path))
    except yaml.YAMLError as error:
        raise ValidationError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(loaded, dict):
        raise ValidationError(f"{path} must contain a YAML mapping")
    return loaded


def _field_ids(
    form: dict[str, Any], path: Path, *, require_fields: bool = True
) -> set[str]:
    body = form.get("body")
    if not isinstance(body, list):
        raise ValidationError(f"{path} must define a body list")
    ids: list[str] = []
    for item in body:
        if not isinstance(item, dict):
            raise ValidationError(f"{path} body entries must be mappings")
        field_id = item.get("id")
        if field_id is None:
            continue
        if not isinstance(field_id, str) or not field_id:
            raise ValidationError(f"{path} contains an invalid field id")
        ids.append(field_id)
        if item.get("type") == "checkboxes":
            options = item.get("attributes", {}).get("options")
            if not isinstance(options, list) or not options or not all(
                isinstance(option, dict) and option.get("required") is True
                for option in options
            ):
                raise ValidationError(f"{path} checkbox {field_id!r} must be required")
            continue
        validations = item.get("validations")
        if require_fields and item.get("type") != "markdown" and (
            not isinstance(validations, dict) or validations.get("required") is not True
        ):
            raise ValidationError(f"{path} field {field_id!r} must be required")
    if len(ids) != len(set(ids)):
        raise ValidationError(f"{path} contains duplicate field ids")
    return set(ids)


def _labels(form: dict[str, Any], path: Path) -> set[str]:
    labels = form.get("labels")
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise ValidationError(f"{path} must define a string label list")
    return set(labels)


def _validate_approval_contract(form: dict[str, Any], path: Path) -> None:
    approval = next(
        (
            item
            for item in form.get("body", [])
            if isinstance(item, dict) and item.get("id") == "approval"
        ),
        None,
    )
    if not isinstance(approval, dict) or approval.get("type") != "checkboxes":
        raise ValidationError(f"{path} must define the approval checkbox")
    options = approval.get("attributes", {}).get("options")
    if options != [{"label": APPROVAL_CONTRACT, "required": True}]:
        raise ValidationError(f"{path} does not use the exact human-maintainer contract")


def _validate_human_field_rendering(form: dict[str, Any], path: Path) -> None:
    fields = {
        item["id"]: item
        for item in form.get("body", [])
        if isinstance(item, dict) and "id" in item
    }
    command_fields = {"starting_commands", "validation_commands"}
    for field_id, item in fields.items():
        render = item.get("attributes", {}).get("render")
        if field_id in command_fields:
            if render != "shell":
                raise ValidationError(f"{path} field {field_id!r} must render as shell")
        elif render is not None:
            raise ValidationError(f"{path} field {field_id!r} must not use shell rendering")


def validate_issue_forms(root: Path = ROOT) -> None:
    issue_dir = root / ".github/ISSUE_TEMPLATE"
    general_path = issue_dir / "work-proposal.yml"
    human_path = issue_dir / "human-first-proposal.yml"
    agent_path = issue_dir / "agent-first-proposal.yml"
    recipe_path = issue_dir / "recipe-proposal.yml"

    expected_yaml = {
        "config.yml",
        "work-proposal.yml",
        "human-first-proposal.yml",
        "agent-first-proposal.yml",
        "recipe-proposal.yml",
    }
    yaml_paths = sorted(issue_dir.glob("*.yml"))
    if {path.name for path in yaml_paths} != expected_yaml:
        raise ValidationError("Issue-template YAML files do not match the chooser contract")
    parsed = {path.name: _load_form(path) for path in yaml_paths}

    general = parsed[general_path.name]
    human = parsed[human_path.name]
    agent = parsed[agent_path.name]
    recipe = parsed[recipe_path.name]
    forms = (general, human, agent, recipe)
    names = {form.get("name") for form in forms}
    if len(names) != 4 or not all(isinstance(name, str) and name for name in names):
        raise ValidationError("Issue chooser names must be unique and non-empty")

    general_labels = _labels(general, general_path)
    if general_labels != {"status:needs-triage"}:
        raise ValidationError("general intake must remain format-neutral")
    if _field_ids(general, general_path) != GENERAL_FORM_FIELDS:
        raise ValidationError("general intake does not implement its field contract")

    expected_forms = (
        (human, human_path, "format:human-first", HUMAN_FORM_FIELDS),
        (agent, agent_path, "format:agent-first", AGENT_FORM_FIELDS),
    )
    for form, path, format_label, fields in expected_forms:
        labels = _labels(form, path)
        if labels != {"status:needs-triage", format_label}:
            raise ValidationError(f"{path} has unexpected initial labels: {sorted(labels)}")
        if _field_ids(form, path) != fields:
            raise ValidationError(f"{path} does not implement its required field contract")
    _validate_human_field_rendering(human, human_path)

    recipe_labels = _labels(recipe, recipe_path)
    required_recipe_labels = {"status:needs-triage", "type:recipe", "area:recipes"}
    if recipe_labels != required_recipe_labels:
        raise ValidationError("recipe intake labels or format-neutral behavior changed")
    if "status:ready" not in _read(recipe_path) or OLD_READY_LABEL in _read(recipe_path):
        raise ValidationError("recipe intake must use status:ready terminology")
    _field_ids(recipe, recipe_path, require_fields=False)
    for form, path in (
        (general, general_path),
        (human, human_path),
        (agent, agent_path),
        (recipe, recipe_path),
    ):
        _validate_approval_contract(form, path)


def _heading_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)
        if not match:
            continue
        heading = re.sub(r"[`*_~]", "", match.group(1)).lower()
        heading = re.sub(r"[^\w\s-]", "", heading)
        heading = re.sub(r"\s+", "-", heading.strip())
        heading = re.sub(r"-+", "-", heading)
        anchors.add(heading)
    return anchors


def validate_markdown_links(root: Path = ROOT) -> None:
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    for relative in DOCS:
        source = root / relative
        text = _read(source)
        for target in link_pattern.findall(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part, separator, anchor = target.partition("#")
            destination = source if not path_part else (source.parent / path_part).resolve()
            if not destination.exists():
                raise ValidationError(f"{relative} links to missing path {path_part!r}")
            if separator and destination.is_file():
                if anchor not in _heading_anchors(_read(destination)):
                    raise ValidationError(
                        f"{relative} links to missing anchor {anchor!r} in {path_part or relative}"
                    )


def validate_documentation(root: Path = ROOT) -> None:
    for relative in DOCS:
        if OLD_READY_LABEL in _read(root / relative):
            raise ValidationError(f"{relative} still contains active old lifecycle terminology")

    combined = "\n".join(_read(root / relative) for relative in DOCS)
    required_fragments = (
        ".github/PULL_REQUEST_TEMPLATE/agent-first.md",
        "?expand=1&template=agent-first.md",
        "format:human-first",
        "format:agent-first",
        "General work proposal",
        "agent-first only",
    )
    for fragment in required_fragments:
        if fragment not in combined:
            raise ValidationError(f"documentation is missing {fragment!r}")
    validate_markdown_links(root)


def validate_repository(root: Path = ROOT) -> None:
    validate_pr_templates(root)
    validate_issue_forms(root)
    validate_documentation(root)


def main() -> int:
    validate_repository()
    print("Contribution templates and guidance are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
