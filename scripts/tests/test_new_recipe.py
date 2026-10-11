"""Run scripts/new-recipe.sh against a temporary checkout; stdlib only."""
import ast
import itertools
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANGUAGES = ("go", "java", "python")
CLASSIFIERS = ("none", "systemone")
AGENTS = ("none", "google-adk", "openai-agents")
VALID = {
    ("go", "none", "none"): "go/bare",
    ("go", "systemone", "none"): "go/systemone",
    ("java", "none", "none"): "java/bare",
    ("java", "systemone", "none"): "java/systemone",
    ("python", "none", "none"): "python/bare",
    ("python", "systemone", "none"): "python/systemone",
    ("python", "none", "google-adk"): "python/google-adk",
    ("python", "systemone", "google-adk"): "python/google-adk-systemone",
    ("python", "none", "openai-agents"): "python/openai-agents",
    ("python", "systemone", "openai-agents"): "python/openai-agents-systemone",
}
IMPLEMENTED = {"python/bare"}
BARE = ("--language", "python", "--classifier", "none", "--agent", "none")
MARKER = re.compile(rb"__[A-Z][A-Z0-9_]*__")
THIRD_PARTY = {"cadence", "grpc", "pydantic"}


def flags(language, classifier, agent):
    return ("--language", language, "--classifier", classifier, "--agent", agent)


def tree(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


class NewRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="new recipe ")
        self.root = self.checkout(Path(self.temp.name) / "checkout")

    def tearDown(self):
        self.temp.cleanup()

    def checkout(self, root):
        (root / "scripts").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/new-recipe.sh", root / "scripts/new-recipe.sh")
        shutil.copytree(ROOT / "templates", root / "templates",
                        ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.egg-info"))
        (root / "recipes").mkdir()
        return root

    def run_script(self, *args, root=None):
        root = root or self.root
        return subprocess.run([str(root / "scripts/new-recipe.sh"), *args], cwd=self.temp.name,
                              capture_output=True, text=True, timeout=30)

    def assertNothingWritten(self, root=None):
        self.assertEqual(list((root or self.root).joinpath("recipes").iterdir()), [])

    def assertUsageError(self, result, message):
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertIn("usage: scripts/new-recipe.sh", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNothingWritten()

    def test_every_combination_is_classified_by_the_matrix(self):
        combinations = list(itertools.product(LANGUAGES, CLASSIFIERS, AGENTS))
        self.assertEqual(len(combinations), 18)
        self.assertEqual(sum(c in VALID for c in combinations), 10)
        for combination in combinations:
            profile = VALID.get(combination)
            if profile in IMPLEMENTED:
                continue
            with self.subTest(combination=combination):
                result = self.run_script("example", *flags(*combination))
                self.assertEqual(result.returncode, 2, result.stderr)
                if profile:
                    self.assertIn(f"{profile} is a valid profile but is not implemented",
                                  result.stderr)
                else:
                    self.assertIn("unsupported combination", result.stderr)
                    self.assertIn("require the Cadence Python SDK", result.stderr)
                self.assertNothingWritten()

    def test_help_prints_the_same_matrix(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0)
        rows = re.findall(r"^  (\w+) +(\S+) +(\S+) +(\S+/\S+)$", result.stdout, re.M)
        self.assertEqual({(l, c, a): p for l, c, a, p in rows}, VALID)
        self.assertIn("Implemented in this checkout: python/bare", result.stdout)

    def test_slug_only_and_missing_options_fail_with_help(self):
        self.assertUsageError(self.run_script("example"),
                              "missing required option(s): --language --classifier --agent")
        self.assertUsageError(self.run_script("example", "--language", "python"),
                              "missing required option(s): --classifier --agent")
        self.assertUsageError(self.run_script(*BARE), "missing recipe slug")
        self.assertUsageError(self.run_script(), "missing recipe slug")

    def test_malformed_options_are_rejected(self):
        cases = {
            ("example", *BARE, "--language", "python"): "--language was given more than once",
            ("example", *BARE, "--template", "x"): "unknown option: --template",
            ("example", "--classifier", "none", "--agent", "none", "--language"):
                "--language needs a value",
            ("example", "--language", "--classifier", "none", "--agent", "none"):
                "--language needs a value",
            ("example", "--language=", "--classifier", "none", "--agent", "none"):
                "--language needs a value",
            ("one", "two", *BARE): "expected one recipe slug, got 'one' and 'two'",
        }
        for args, message in cases.items():
            with self.subTest(args=args):
                self.assertUsageError(self.run_script(*args), message)

    def test_unknown_values_and_bad_slugs_are_rejected(self):
        for args, message in {
            flags("rust", "none", "none"): "unknown --language 'rust'",
            flags("Python", "none", "none"): "unknown --language 'Python'",
            flags("python", "laya", "none"): "unknown --classifier 'laya'",
            flags("python", "none", "langchain"): "unknown --agent 'langchain'",
        }.items():
            with self.subTest(args=args):
                self.assertUsageError(self.run_script("example", *args), message)
        for slug in ("Bad", "1abc", "a--b", "a-", "a_b", "../escape", "a/b"):
            with self.subTest(slug=slug):
                self.assertUsageError(self.run_script(slug, *BARE), "recipe slug must start")

    def test_python_bare_generation_and_next_steps(self):
        result = self.run_script("invoice-review", "--agent=none", "--classifier", "none",
                                 "--language", "python")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, (
            "created recipes/invoice-review from profile python/bare\n"
            "\n"
            "Next steps (Python 3.12 or 3.13):\n"
            "  cd recipes/invoice-review/python/bare\n"
            "  python3 -m venv .venv\n"
            "  source .venv/bin/activate\n"
            "  python -m pip install -e .\n"
            "  python -m unittest discover -s tests -v\n"
            "\n"
            "Then complete recipes/invoice-review/SPEC.md and replace the starter Workflow.\n"
            "Run instructions: recipes/invoice-review/README.md\n"))
        recipe = self.root / "recipes/invoice-review"
        self.assertEqual([p.name for p in (self.root / "recipes").iterdir()], ["invoice-review"])
        self.assertEqual(sorted(tree(recipe)), [
            "PROMPT.md", "README.md", "SPEC.md",
            "python/bare/main.py", "python/bare/pyproject.toml",
            "python/bare/tests/test_recipe.py", "python/bare/workflow.py",
        ])
        for name, content in tree(recipe).items():
            self.assertIsNone(MARKER.search(content), name)
            self.assertIsNone(MARKER.search(name.encode()), name)
        self.assertTrue((recipe / "README.md").read_text().startswith("# Invoice Review\n"))
        self.assertIn("class InvoiceReviewWorkflow:",
                      (recipe / "python/bare/workflow.py").read_text())
        spec = (recipe / "SPEC.md").read_text()
        for field in ("**Cadence language:** Python", "**Classifier integration:** none",
                      "**Agent integration:** none", "invoice-review-demo"):
            self.assertIn(field, spec)
        main = (recipe / "python/bare/main.py").read_text()
        self.assertIn('DEFAULT_TASK_LIST = "invoice-review"', main)
        self.assertIn('DEFAULT_WORKFLOW_ID = "invoice-review-demo"', main)
        self.assertIn('domain", default="cadence-ai-samples"', main)
        self.assertIn('name = "invoice-review"', (recipe / "python/bare/pyproject.toml").read_text())

    def test_generated_markdown_links_resolve_inside_the_recipe(self):
        self.assertEqual(self.run_script("alpha", *BARE).returncode, 0)
        recipe = (self.root / "recipes/alpha").resolve()
        for path in recipe.rglob("*.md"):
            for target in re.findall(r"\]\(([^)\s]+)\)", path.read_text()):
                if re.match(r"https?://", target) or target.startswith("#"):
                    continue
                resolved = (path.parent / target.split("#")[0]).resolve()
                with self.subTest(file=path.name, target=target):
                    self.assertTrue(resolved.is_relative_to(recipe))
                    self.assertTrue(resolved.exists())
        self.assertIn("(https://github.com/cadence-workflow/cadence-ai-samples/blob/main/"
                      "templates/SPEC-Recipe-Templates.md)", (recipe / "SPEC.md").read_text())

    def test_generated_imports_stay_inside_the_recipe(self):
        self.assertEqual(self.run_script("alpha", *BARE).returncode, 0)
        implementation = self.root / "recipes/alpha/python/bare"
        local = {p.stem for p in implementation.glob("*.py")}
        allowed = set(sys.stdlib_module_names) | THIRD_PARTY | local
        for path in implementation.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0, f"relative import in {path.name}")
                    modules = [node.module]
                else:
                    continue
                for module in modules:
                    self.assertIn(module.split(".")[0], allowed, f"{path.name} imports {module}")
            self.assertNotIn("recipes", path.read_text().replace("recipes/alpha", ""), path.name)

    def test_repeated_generation_and_overwrite_refusal(self):
        self.assertEqual(self.run_script("alpha", *BARE).returncode, 0)
        self.assertEqual(self.run_script("beta", *BARE).returncode, 0)
        before = tree(self.root / "recipes/alpha")
        (self.root / "recipes/alpha/README.md").write_text("# Edited\n")
        before["README.md"] = b"# Edited\n"
        result = self.run_script("alpha", *BARE)
        self.assertEqual(result.returncode, 1)
        self.assertIn("destination already exists: recipes/alpha", result.stderr)
        self.assertEqual(tree(self.root / "recipes/alpha"), before)
        (self.root / "recipes/file").write_text("not a recipe")
        os.symlink(self.root / "recipes/beta", self.root / "recipes/link")
        for slug in ("file", "link"):
            with self.subTest(slug=slug):
                self.assertEqual(self.run_script(slug, *BARE).returncode, 1)
        self.assertEqual(sorted(p.name for p in (self.root / "recipes").iterdir()),
                         ["alpha", "beta", "file", "link"])

    def test_generation_is_deterministic(self):
        other = self.checkout(Path(self.temp.name) / "other")
        self.assertEqual(self.run_script("alpha", *BARE).returncode, 0)
        self.assertEqual(self.run_script("alpha", *BARE, root=other).returncode, 0)
        self.assertEqual(tree(self.root / "recipes/alpha"), tree(other / "recipes/alpha"))

    def test_local_artifacts_in_a_template_are_not_copied(self):
        implementation = self.root / "templates/profiles/python/bare/recipe/python/bare"
        for artifact in (".venv/bin/python", "__pycache__/main.cpython-313.pyc",
                         "x.egg-info/PKG-INFO", ".DS_Store"):
            (implementation / artifact).parent.mkdir(parents=True, exist_ok=True)
            (implementation / artifact).write_bytes(b"\x00__ARTIFACT__")
        self.assertEqual(self.run_script("alpha", *BARE).returncode, 0)
        names = set(tree(self.root / "recipes/alpha"))
        self.assertFalse({n for n in names if re.search(r"\.venv|__pycache__|egg-info|DS_Store", n)})

    def test_unresolved_placeholder_aborts_without_partial_output(self):
        template = self.root / "templates/profiles/python/bare/recipe"
        (template / "NOTES.md").write_text("Owner: __RECIPE_OWNER__\n")
        result = self.run_script("alpha", *BARE)
        self.assertEqual(result.returncode, 1)
        self.assertIn("unresolved template placeholders in: NOTES.md", result.stderr)
        self.assertNothingWritten()
        (template / "NOTES.md").unlink()
        (template / "__RECIPE_OWNER__.md").write_text("ok\n")
        result = self.run_script("alpha", *BARE)
        self.assertEqual(result.returncode, 1)
        self.assertIn("unresolved template placeholder in path", result.stderr)
        self.assertNothingWritten()

    def test_template_directories_match_implemented_profiles(self):
        source = (ROOT / "scripts/new-recipe.sh").read_text()
        declared = set(re.search(r'^implemented_profiles="([^"]*)"', source, re.M)[1].split())
        self.assertEqual(declared, IMPLEMENTED)
        profiles = ROOT / "templates/profiles"
        found = {str(p.parent.relative_to(profiles)) for p in profiles.glob("*/*/next-steps.txt")}
        self.assertEqual(found, IMPLEMENTED)
        for profile in found:
            self.assertTrue((profiles / profile / "recipe/README.md").is_file())
            self.assertTrue((profiles / profile / "recipe/SPEC.md").is_file())
        self.assertFalse((ROOT / "templates/recipe").exists())


if __name__ == "__main__":
    unittest.main()
