"""Focused contracts for contribution Issue and pull-request formats."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_contribution_templates as contracts  # noqa: E402


class ContributionTemplateTests(unittest.TestCase):
    def test_pyyaml_version_is_pinned(self):
        self.assertEqual("6.0.2", yaml.__version__)

    def test_repository_contracts_pass(self):
        contracts.validate_repository(ROOT)

    def test_every_mandatory_governance_gate_is_in_each_template(self):
        templates = {
            "human-first": (
                ROOT / ".github/PULL_REQUEST_TEMPLATE.md"
            ).read_text(encoding="utf-8"),
            "agent-first": (
                ROOT / ".github/PULL_REQUEST_TEMPLATE/agent-first.md"
            ).read_text(encoding="utf-8"),
        }
        for template_name, template in templates.items():
            for gate_name, fragments in contracts.MANDATORY_GOVERNANCE_GATES.items():
                for fragment in fragments:
                    with self.subTest(template=template_name, gate=gate_name):
                        self.assertIn(fragment, template)

    def test_removing_one_gate_fails(self):
        human = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
        agent = (
            ROOT / ".github/PULL_REQUEST_TEMPLATE/agent-first.md"
        ).read_text(encoding="utf-8")
        changed_agent = agent.replace(
            "- [ ] Commits include DCO sign-off (`git commit -s`).\n", "", 1
        )

        with self.assertRaisesRegex(contracts.ValidationError, "blocks differ"):
            contracts.validate_governance_texts(human, changed_agent)

    def test_changing_the_same_gate_in_both_templates_fails(self):
        human = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
        agent = (
            ROOT / ".github/PULL_REQUEST_TEMPLATE/agent-first.md"
        ).read_text(encoding="utf-8")
        old_gate = "- [ ] The branch is current with upstream `main`."
        new_gate = "- [ ] The branch should usually be current with upstream `main`."

        with self.assertRaisesRegex(contracts.ValidationError, "mandatory contract"):
            contracts.validate_governance_texts(
                human.replace(old_gate, new_gate, 1),
                agent.replace(old_gate, new_gate, 1),
            )

    def test_human_first_form_accepts_a_gap_without_manufacturing_failure(self):
        form_path = ROOT / ".github/ISSUE_TEMPLATE/human-first-proposal.yml"
        form = yaml.safe_load(form_path.read_text(encoding="utf-8"))
        fields = {
            item["id"]: item
            for item in form["body"]
            if isinstance(item, dict) and "id" in item
        }

        self.assertEqual("shell", fields["starting_commands"]["attributes"]["render"])
        self.assertNotIn("render", fields["observable_gap"]["attributes"])
        self.assertIn(
            "failure or missing capability",
            fields["observable_gap"]["attributes"]["description"],
        )
        self.assertEqual("shell", fields["validation_commands"]["attributes"]["render"])
        self.assertNotIn("render", fields["expected_success"]["attributes"])
        self.assertIn(
            "future unfinished handoff",
            fields["handoff_requirements"]["attributes"]["description"],
        )

    def test_general_work_proposal_is_format_neutral(self):
        form_path = ROOT / ".github/ISSUE_TEMPLATE/work-proposal.yml"
        form = yaml.safe_load(form_path.read_text(encoding="utf-8"))

        self.assertEqual(["status:needs-triage"], form["labels"])

    def test_every_form_has_exact_human_maintainer_approval_contract(self):
        form_names = (
            "work-proposal.yml",
            "human-first-proposal.yml",
            "agent-first-proposal.yml",
            "recipe-proposal.yml",
        )
        for form_name in form_names:
            form_path = ROOT / ".github/ISSUE_TEMPLATE" / form_name
            form = yaml.safe_load(form_path.read_text(encoding="utf-8"))
            approval = next(item for item in form["body"] if item.get("id") == "approval")
            with self.subTest(form=form_name):
                self.assertEqual(
                    [
                        {
                            "label": contracts.APPROVAL_CONTRACT,
                            "required": True,
                        }
                    ],
                    approval["attributes"]["options"],
                )

    def test_agent_first_creation_paths_are_explicit(self):
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

        self.assertIn(
            "gh pr create --template .github/PULL_REQUEST_TEMPLATE/agent-first.md",
            contributing,
        )
        self.assertIn("?expand=1&template=agent-first.md", contributing)
        self.assertIn("does not automatically display a template chooser", contributing)


if __name__ == "__main__":
    unittest.main()
