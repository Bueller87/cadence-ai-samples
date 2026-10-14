"""Run scripts/add-recipe-implementation.sh against temporary checkouts; stdlib only."""
import itertools
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_new_recipe import (
    AGENTS,
    CLASSIFIERS,
    IMPLEMENTED,
    LANGUAGES,
    MARKER,
    PROFILE_CHOICES,
    ROOT,
    VALID,
    flags,
    tree,
)

ARTIFACT_CASES = {
    ".venv/bin/python": ".venv",
    "__pycache__/main.cpython-313.pyc": "__pycache__",
    ".gradle/cache.bin": ".gradle",
    "build/classes/Recipe.class": "build",
    "x.egg-info/PKG-INFO": "x.egg-info",
    ".DS_Store": ".DS_Store",
}


def render_next_steps(slug, profile):
    title = " ".join(part.capitalize() for part in slug.split("-"))
    class_name = "".join(part.capitalize() for part in slug.split("-"))
    text = (ROOT / "templates/profiles" / profile / "next-steps.txt").read_text()
    return (
        text.replace("__RECIPE_SLUG__", slug)
        .replace("__RECIPE_TITLE__", title)
        .replace("__RECIPE_CLASS__", class_name)
    )


def expected_success_stdout(slug, profile):
    return (
        f"added recipes/{slug}/{profile} from profile {profile}\n\n"
        f"{render_next_steps(slug, profile)}"
    )


def snapshot(recipe_root, prefix=""):
    """Relative path -> (bytes, executable bit) for files."""
    out = {}
    for path in sorted(recipe_root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(recipe_root))
        if prefix and (rel == prefix or rel.startswith(prefix + "/")):
            continue
        mode = path.stat().st_mode
        out[rel] = (path.read_bytes(), mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    return out


def path_snapshot(recipe_root):
    """Relative path -> kind for every file, directory, and symlink."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(recipe_root):
        root_path = Path(dirpath)
        rel_root = str(root_path.relative_to(recipe_root))
        if rel_root == ".":
            rel_root = ""
        if rel_root not in out:
            out[rel_root] = ("dir",)
        for name in dirnames:
            out[str(Path(rel_root) / name) if rel_root else name] = ("dir",)
        for name in filenames:
            path = root_path / name
            rel = str(path.relative_to(recipe_root))
            if path.is_symlink():
                out[rel] = ("symlink", os.readlink(path))
            else:
                mode = path.stat().st_mode
                out[rel] = (
                    "file",
                    path.read_bytes(),
                    mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH),
                )
    return out


def extract_generator_constants(script_text):
    languages = script_text.split('languages="', 1)[1].split('"', 1)[0].split()
    classifiers = script_text.split('classifiers="', 1)[1].split('"', 1)[0].split()
    agents = script_text.split('agents="', 1)[1].split('"', 1)[0].split()
    implemented = set(script_text.split('implemented_profiles="', 1)[1].split('"', 1)[0].split())
    cases = re.findall(
        r"^\s+(\w+)/(\w+)/(\w+)\) echo (\S+/\S+) ;;",
        script_text,
        re.M,
    )
    matrix = {(lang, cls, agt): profile for lang, cls, agt, profile in cases}
    return languages, classifiers, agents, implemented, matrix


class AddRecipeImplementationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="add recipe implementation ")
        self.root = self.checkout(Path(self.temp.name) / "checkout")

    def tearDown(self):
        self.temp.cleanup()

    def checkout(self, root):
        (root / "scripts").mkdir(parents=True)
        for name in (
            "new-recipe.sh",
            "add-recipe-implementation.sh",
            "recipe_implementation_validate.py",
        ):
            shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
        shutil.copytree(
            ROOT / "templates",
            root / "templates",
            ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.egg-info"),
        )
        (root / "recipes").mkdir()
        return root

    def run_new_recipe(self, *args, root=None):
        root = root or self.root
        return subprocess.run(
            [str(root / "scripts/new-recipe.sh"), *args],
            cwd=self.temp.name,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def run_add(self, *args, root=None):
        root = root or self.root
        return subprocess.run(
            [str(root / "scripts/add-recipe-implementation.sh"), *args],
            cwd=self.temp.name,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def patch_add_script(self, root, needle, replacement):
        path = root / "scripts/add-recipe-implementation.sh"
        path.write_text(path.read_text().replace(needle, replacement, 1))

    def assertUsageError(self, result, message):
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertIn("usage: scripts/add-recipe-implementation.sh", result.stderr)
        self.assertEqual(result.stdout, "")

    def seed_existing_recipe(self, slug, existing_profile, root=None):
        root = root or self.root
        choices = PROFILE_CHOICES[existing_profile]
        result = self.run_new_recipe(slug, *flags(*choices), root=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        return root / "recipes" / slug

    def reference_implementation(self, slug, profile):
        other = self.checkout(Path(self.temp.name) / f"ref-{profile.replace('/', '-')}")
        result = self.run_new_recipe(slug, *flags(*PROFILE_CHOICES[profile]), root=other)
        self.assertEqual(result.returncode, 0, result.stderr)
        impl = other / "recipes" / slug / profile
        return tree(impl)

    def test_matrix_parity_with_new_recipe(self):
        new_text = (ROOT / "scripts/new-recipe.sh").read_text()
        add_text = (ROOT / "scripts/add-recipe-implementation.sh").read_text()
        self.assertEqual(extract_generator_constants(new_text), extract_generator_constants(add_text))

    def test_help_prints_the_same_matrix(self):
        result = self.run_add("--help")
        self.assertEqual(result.returncode, 0)
        rows = re.findall(r"^  (\w+) +(\S+) +(\S+) +(\S+/\S+)$", result.stdout, re.M)
        self.assertEqual({(l, c, a): p for l, c, a, p in rows}, VALID)

    def test_next_steps_output_for_every_profile_family(self):
        for profile, choices in PROFILE_CHOICES.items():
            slug = f"next-{profile.replace('/', '-')}"
            existing = next(p for p in PROFILE_CHOICES if p != profile)
            with self.subTest(profile=profile):
                self.seed_existing_recipe(slug, existing)
                result = self.run_add(slug, *flags(*choices))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected_success_stdout(slug, profile))

    def test_missing_recipe_and_invalid_roots_fail_before_writing(self):
        self.assertIn(
            "recipe root does not exist",
            self.run_add("missing", *flags(*PROFILE_CHOICES["python/bare"])).stderr,
        )
        (self.root / "recipes/file-root").write_text("not a directory")
        self.assertIn(
            "recipe root is not a directory",
            self.run_add("file-root", *flags(*PROFILE_CHOICES["python/bare"])).stderr,
        )
        os.symlink(self.root / "recipes", self.root / "recipes/link-root")
        self.assertIn(
            "recipe root is a symlink",
            self.run_add("link-root", *flags(*PROFILE_CHOICES["python/bare"])).stderr,
        )

    def test_missing_required_recipe_root_files_fail_before_writing(self):
        slug = "incomplete-root"
        recipe = self.seed_existing_recipe(slug, "python/bare")
        (recipe / "PROMPT.md").unlink()
        before = path_snapshot(recipe)
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]))
        self.assertEqual(result.returncode, 1)
        self.assertIn("recipe root is missing required file: recipes/incomplete-root/PROMPT.md",
                      result.stderr)
        self.assertEqual(before, path_snapshot(recipe))

    def test_intermediate_destination_component_that_is_a_file(self):
        slug = "file-component"
        recipe = self.seed_existing_recipe(slug, "python/bare")
        (recipe / "java").write_text("blocker")
        before = path_snapshot(recipe)
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["java/bare"]))
        self.assertEqual(result.returncode, 1)
        self.assertIn("implementation destination path is blocked by a file", result.stderr)
        self.assertEqual(before, path_snapshot(recipe))

    def test_invalid_options_match_new_recipe(self):
        bare = flags(*PROFILE_CHOICES["python/bare"])
        self.assertUsageError(
            self.run_add("example"),
            "missing required option(s): --language --classifier --agent",
        )
        self.assertUsageError(self.run_add("BadSlug", *bare), "recipe slug must start")

    def test_unimplemented_and_unsupported_combinations_fail(self):
        slug = "combo-check"
        self.seed_existing_recipe(slug, "python/bare")
        for combination in itertools.product(LANGUAGES, CLASSIFIERS, AGENTS):
            profile = VALID.get(combination)
            if profile in IMPLEMENTED:
                continue
            with self.subTest(combination=combination):
                before = path_snapshot(self.root / "recipes" / slug)
                result = self.run_add(slug, *flags(*combination))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(before, path_snapshot(self.root / "recipes" / slug))

    def test_every_implemented_profile_adds_only_the_implementation_tree(self):
        for profile, choices in PROFILE_CHOICES.items():
            slug = f"add-{profile.replace('/', '-')}"
            existing = next(p for p in PROFILE_CHOICES if p != profile)
            with self.subTest(profile=profile):
                recipe = self.seed_existing_recipe(slug, existing)
                before = snapshot(recipe)
                result = self.run_add(slug, *flags(*choices))
                self.assertEqual(result.returncode, 0, result.stderr)
                after = snapshot(recipe)
                self.assertEqual(before, {k: after[k] for k in before})
                expected = self.reference_implementation(slug, profile)
                self.assertEqual(tree(recipe / profile), expected)
                for name, content in tree(recipe / profile).items():
                    self.assertIsNone(MARKER.search(content), name)
                if profile.startswith("java/"):
                    self.assertTrue(os.access(recipe / profile / "gradlew", os.X_OK))

    def test_agent_handoffs_fixture_command(self):
        slug = "agent-handoffs"
        recipe = self.seed_existing_recipe(slug, "python/openai-agents")
        before = path_snapshot(recipe)
        result = self.run_add(
            slug,
            "--language",
            "python",
            "--classifier",
            "none",
            "--agent",
            "google-adk",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected_success_stdout(slug, "python/google-adk"))
        after = path_snapshot(recipe)
        self.assertEqual(before, {k: after[k] for k in before})

    def test_existing_destination_and_symlink_paths_are_rejected(self):
        slug = "blocked"
        recipe = self.seed_existing_recipe(slug, "python/bare")
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/bare"]))
        self.assertEqual(result.returncode, 1)
        self.assertIn("implementation destination already exists", result.stderr)
        (recipe / "python").mkdir(exist_ok=True)
        os.symlink(recipe / "python/bare", recipe / "python/google-adk")
        before = path_snapshot(recipe)
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]))
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to write through symlink", result.stderr)
        self.assertEqual(before, path_snapshot(recipe))

    def test_cross_recipe_python_import_fails(self):
        slug = "cross-recipe-python"
        recipe = self.seed_existing_recipe(slug, "python/openai-agents")
        template = self.root / "templates/profiles/python/google-adk/recipe/python/google-adk"
        path = template / "cross_recipe.py"
        path.write_text("import recipes.otherrecipe\n")
        before = path_snapshot(recipe)
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("cross-recipe import reference: cross_recipe.py: import recipes.otherrecipe",
                      result.stderr)
        self.assertEqual(before, path_snapshot(recipe))
        path.unlink()

    def test_cross_recipe_go_import_fails(self):
        slug = "cross-recipe-go"
        root = self.checkout(Path(self.temp.name) / "cross-go")
        recipe = self.seed_existing_recipe(slug, "python/openai-agents", root=root)
        template = root / "templates/profiles/python/google-adk/recipe/python/google-adk"
        path = template / "cross_recipe.go"
        path.write_text(
            'import _ "github.com/cadence-workflow/cadence-ai-samples/recipes/other-recipe/go/bare"\n'
        )
        before = path_snapshot(recipe)
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]), root=root)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("cross-recipe import reference", result.stderr)
        self.assertIn("recipes/other-recipe", result.stderr)
        self.assertEqual(before, path_snapshot(recipe))
        path.unlink()

    def test_cross_recipe_java_import_fails(self):
        slug = "cross-recipe-java"
        root = self.checkout(Path(self.temp.name) / "cross-java")
        recipe = self.seed_existing_recipe(slug, "python/openai-agents", root=root)
        template = root / "templates/profiles/python/google-adk/recipe/python/google-adk"
        path = template / "CrossRecipe.java"
        path.write_text("import recipes.otherrecipe.Workflow;\n")
        before = path_snapshot(recipe)
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]), root=root)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("cross-recipe import reference", result.stderr)
        self.assertIn("import recipes.otherrecipe.Workflow", result.stderr)
        self.assertEqual(before, path_snapshot(recipe))
        path.unlink()

    def test_empty_artifact_directory_fail_closed(self):
        slug = "empty-artifact"
        root = self.checkout(Path(self.temp.name) / "empty-artifact")
        self.seed_existing_recipe(slug, "python/bare", root=root)
        impl = root / "templates/profiles/python/google-adk/recipe/python/google-adk"
        (impl / ".venv").mkdir()
        before = path_snapshot(root / "recipes/empty-artifact")
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]), root=root)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("generated artifact in profile source: .venv", result.stderr)
        self.assertEqual(before, path_snapshot(root / "recipes/empty-artifact"))
        (impl / ".venv").rmdir()

    def test_implementation_source_symlink_outside_profile_template(self):
        slug = "outside-source"
        root = self.checkout(Path(self.temp.name) / "outside-source")
        self.seed_existing_recipe(slug, "python/bare", root=root)
        profile_root = root / "templates/profiles/python/google-adk"
        implementation = profile_root / "recipe/python/google-adk"
        outside = root / "outside-implementation"
        shutil.move(str(implementation), str(outside))
        os.symlink(outside, implementation)
        before = path_snapshot(root / "recipes/outside-source")
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]), root=root)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(
            any(
                phrase in result.stderr
                for phrase in (
                    "profile implementation source is a symlink",
                    "profile implementation source resolves outside profile template",
                    "profile template path is a symlink",
                )
            ),
            result.stderr,
        )
        self.assertEqual(before, path_snapshot(root / "recipes/outside-source"))
        implementation.unlink()
        shutil.move(str(outside), str(implementation))

    def test_unresolved_placeholder_in_file_and_path(self):
        slug = "placeholder"
        recipe = self.seed_existing_recipe(slug, "python/openai-agents")
        template = self.root / "templates/profiles/python/google-adk/recipe/python/google-adk"
        cases = (
            ("NOTES.md", "Owner: __RECIPE_OWNER__\n", "unresolved template placeholders"),
            ("__RECIPE_OWNER__.md", "ok\n", "unresolved template placeholder in path"),
        )
        for name, content, message in cases:
            path = template / name
            path.write_text(content)
            with self.subTest(name=name):
                before = path_snapshot(recipe)
                result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]))
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertEqual(before, path_snapshot(recipe))
            path.unlink(missing_ok=True)

    def test_symlinks_in_profile_source_and_staged_implementation(self):
        slug = "symlink"
        recipe = self.seed_existing_recipe(slug, "python/bare")
        template = self.root / "templates/profiles/python/google-adk/recipe/python/google-adk"
        before = path_snapshot(recipe)
        os.symlink(template / "main.py", template / "link-main.py")
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]))
        self.assertEqual(result.returncode, 1)
        self.assertIn("symlink in profile source", result.stderr)
        self.assertEqual(before, path_snapshot(recipe))
        (template / "link-main.py").unlink()
        self.patch_add_script(
            self.root,
            'run_tree_validation "$staging" staged',
            'ln -s main.py "$staging/link-after-copy.py"\nrun_tree_validation "$staging" staged',
        )
        before = path_snapshot(recipe)
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]))
        self.assertEqual(result.returncode, 1)
        self.assertIn("symlink in staged implementation", result.stderr)
        self.assertEqual(before, path_snapshot(recipe))

    def test_generated_artifact_classes_fail_closed(self):
        slug = "artifacts"
        for artifact, message in ARTIFACT_CASES.items():
            with self.subTest(artifact=artifact):
                root = self.checkout(Path(self.temp.name) / f"artifact-{artifact.replace('/', '-')}")
                self.seed_existing_recipe(slug, "python/bare", root=root)
                impl = root / "templates/profiles/python/google-adk/recipe/python/google-adk"
                path = impl / artifact
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"\x00__ARTIFACT__")
                before = path_snapshot(root / "recipes/artifacts")
                result = self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]), root=root)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("generated artifact in profile source:", result.stderr)
                self.assertIn(message, result.stderr)
                self.assertEqual(before, path_snapshot(root / "recipes/artifacts"))

    def test_injected_render_and_move_failures_cleanup_created_parents(self):
        slug = "cleanup"
        recipe = self.seed_existing_recipe(slug, "python/bare")
        self.patch_add_script(
            self.root,
            'while IFS= read -r -d \'\' file; do',
            'if [[ -f "$staging/.inject-render-fail" ]]; then fail 1 "injected render failure"; fi\n'
            'while IFS= read -r -d \'\' file; do',
        )
        template = self.root / "templates/profiles/java/bare/recipe/java/bare"
        (template / ".inject-render-fail").write_text("")
        before = path_snapshot(recipe)
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["java/bare"]))
        self.assertEqual(result.returncode, 1)
        self.assertIn("injected render failure", result.stderr)
        self.assertEqual(before, path_snapshot(recipe))
        (template / ".inject-render-fail").unlink()

        other = self.checkout(Path(self.temp.name) / "cleanup-move")
        recipe = self.seed_existing_recipe(slug, "python/bare", root=other)
        self.patch_add_script(
            other,
            'if ! mv -- "$staging" "$destination"; then',
            'if [[ -f "$recipe_root/.inject-move-fail" ]]; then fail 1 "injected move failure"; fi\n'
            'if ! mv -- "$staging" "$destination"; then',
        )
        (recipe / ".inject-move-fail").write_text("")
        before = path_snapshot(recipe)
        result = self.run_add(slug, *flags(*PROFILE_CHOICES["java/bare"]), root=other)
        self.assertEqual(result.returncode, 1)
        self.assertIn("injected move failure", result.stderr)
        self.assertEqual(before, path_snapshot(recipe))
        self.assertFalse((recipe / "java").exists())

    def test_generation_is_deterministic(self):
        slug = "deterministic"
        self.seed_existing_recipe(slug, "python/openai-agents")
        other = self.checkout(Path(self.temp.name) / "other")
        self.seed_existing_recipe(slug, "python/openai-agents", root=other)
        self.assertEqual(
            self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"])).returncode,
            0,
        )
        self.assertEqual(
            self.run_add(slug, *flags(*PROFILE_CHOICES["python/google-adk"]), root=other)
            .returncode,
            0,
        )
        self.assertEqual(
            tree(self.root / "recipes/deterministic/python/google-adk"),
            tree(other / "recipes/deterministic/python/google-adk"),
        )


if __name__ == "__main__":
    unittest.main()
