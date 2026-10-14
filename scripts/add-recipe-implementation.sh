#!/usr/bin/env bash
set -euo pipefail

languages="go java python"
classifiers="none systemone"
agents="none google-adk openai-agents"
implemented_profiles="go/bare go/systemone java/bare java/systemone python/bare python/systemone python/google-adk python/google-adk-systemone python/openai-agents python/openai-agents-systemone"
required_root_files="README.md SPEC.md PROMPT.md"

profile_for() {
  case "$1/$2/$3" in
    go/none/none) echo go/bare ;;
    go/systemone/none) echo go/systemone ;;
    java/none/none) echo java/bare ;;
    java/systemone/none) echo java/systemone ;;
    python/none/none) echo python/bare ;;
    python/systemone/none) echo python/systemone ;;
    python/none/google-adk) echo python/google-adk ;;
    python/systemone/google-adk) echo python/google-adk-systemone ;;
    python/none/openai-agents) echo python/openai-agents ;;
    python/systemone/openai-agents) echo python/openai-agents-systemone ;;
    *) return 1 ;;
  esac
}

print_matrix() {
  printf '  %-9s %-11s %-14s %s\n' language classifier agent profile
  local language classifier agent profile
  for language in $languages; do
    for classifier in $classifiers; do
      for agent in $agents; do
        if profile=$(profile_for "$language" "$classifier" "$agent"); then
          printf '  %-9s %-11s %-14s %s\n' "$language" "$classifier" "$agent" "$profile"
        fi
      done
    done
  done
}

usage() {
  cat <<EOF
usage: scripts/add-recipe-implementation.sh <existing-recipe-slug> --language LANGUAGE --classifier CLASSIFIER --agent AGENT

Add one implementation profile directory to an existing recipes/<slug> tree.
Recipe-owned root documents and sibling implementations are left unchanged.

  --language    ${languages// / | }
  --classifier  ${classifiers// / | }
  --agent       ${agents// / | }

Valid combinations:

$(print_matrix)

Implemented in this checkout: $implemented_profiles

Example:
  scripts/add-recipe-implementation.sh agent-handoffs --language python --classifier none --agent google-adk
EOF
}

usage_error() {
  echo "add-recipe-implementation: $1" >&2
  echo >&2
  usage >&2
  cleanup_failure
  exit 2
}

staging=""
created_parent_dirs=()

cleanup_failure() {
  if [[ -n $staging && -e $staging ]]; then
    rm -rf -- "$staging"
    staging=""
  fi
  if ((${#created_parent_dirs[@]} > 0)); then
    local idx dir
    for ((idx = ${#created_parent_dirs[@]} - 1; idx >= 0; idx--)); do
      dir=${created_parent_dirs[idx]}
      if [[ -d $dir ]] && [[ -z $(ls -A "$dir" 2>/dev/null || true) ]]; then
        rmdir -- "$dir" 2>/dev/null || true
      fi
    done
    created_parent_dirs=()
  fi
}

fail() {
  echo "add-recipe-implementation: $2" >&2
  cleanup_failure
  exit "$1"
}

contains() {
  local item
  for item in $2; do
    if [[ $item == "$1" ]]; then
      return 0
    fi
  done
  return 1
}

run_tree_validation() {
  local target=$1 phase=$2
  if ! output=$(python3 "$script_dir/recipe_implementation_validate.py" "$target" "$slug" "$phase" 2>&1); then
    fail 1 "$output"
  fi
}

slug=""
language=""
classifier=""
agent=""

set_once() {
  local name=$1 value=$2
  if [[ -n ${!name} ]]; then
    usage_error "--$name was given more than once"
  fi
  if [[ -z $value || $value == -* ]]; then
    usage_error "--$name needs a value"
  fi
  printf -v "$name" '%s' "$value"
}

while [[ $# -gt 0 ]]; do
  case $1 in
    -h | --help)
      usage
      exit 0
      ;;
    --language | --classifier | --agent)
      set_once "${1#--}" "${2:-}"
      shift 2
      ;;
    --language=* | --classifier=* | --agent=*)
      option=${1%%=*}
      set_once "${option#--}" "${1#*=}"
      shift
      ;;
    -*)
      usage_error "unknown option: $1"
      ;;
    *)
      if [[ -n $slug ]]; then
        usage_error "expected one recipe slug, got '$slug' and '$1'"
      fi
      slug=$1
      shift
      ;;
  esac
done

if [[ -z $slug ]]; then
  usage_error "missing recipe slug"
fi
missing=""
for name in language classifier agent; do
  if [[ -z ${!name} ]]; then
    missing+=" --$name"
  fi
done
if [[ -n $missing ]]; then
  usage_error "missing required option(s):$missing"
fi
if [[ ! $slug =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)*$ ]]; then
  usage_error "recipe slug must start with a letter and use lowercase letters, numbers, and single hyphens: '$slug'"
fi
contains "$language" "$languages" ||
  usage_error "unknown --language '$language'; expected one of: $languages"
contains "$classifier" "$classifiers" ||
  usage_error "unknown --classifier '$classifier'; expected one of: $classifiers"
contains "$agent" "$agents" ||
  usage_error "unknown --agent '$agent'; expected one of: $agents"

if ! profile=$(profile_for "$language" "$classifier" "$agent"); then
  hint=""
  if [[ $agent != none ]]; then
    hint=" Agent integrations currently require the Cadence Python SDK."
  fi
  usage_error "unsupported combination: --language $language --classifier $classifier --agent $agent.$hint"
fi
if ! contains "$profile" "$implemented_profiles"; then
  fail 2 "$profile is a valid profile but is not implemented in this checkout. Implemented profiles: $implemented_profiles"
fi

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
template="$repo_root/templates/profiles/$profile"
recipes_dir=$(CDPATH='' cd -- "$repo_root/recipes" && pwd)
recipe_entry="$recipes_dir/$slug"

if [[ -L $recipe_entry ]]; then
  fail 1 "recipe root is a symlink: recipes/$slug"
fi
if [[ ! -e $recipe_entry ]]; then
  fail 1 "recipe root does not exist: recipes/$slug"
fi
if [[ ! -d $recipe_entry ]]; then
  fail 1 "recipe root is not a directory: recipes/$slug"
fi
recipe_root=$(CDPATH='' cd -- "$recipe_entry" && pwd)
if [[ $recipe_root != "$recipes_dir"/* ]]; then
  fail 1 "recipe root resolves outside recipes/: recipes/$slug"
fi

for required in $required_root_files; do
  if [[ ! -f $recipe_root/$required ]]; then
    fail 1 "recipe root is missing required file: recipes/$slug/$required"
  fi
done

if [[ ! -d $template/recipe || ! -f $template/next-steps.txt ]]; then
  fail 1 "profile template is incomplete: templates/profiles/$profile"
fi
implementation_source="$template/recipe/$profile"
if [[ ! -e $implementation_source ]]; then
  fail 1 "profile template is incomplete: templates/profiles/$profile/recipe/$profile"
fi

template_walk="$template"
IFS=/ read -r -a profile_parts <<< "$profile"
for template_part in recipe "${profile_parts[@]}"; do
  template_walk="$template_walk/$template_part"
  if [[ -L $template_walk ]]; then
    fail 1 "profile template path is a symlink: ${template_walk#"$repo_root"/}"
  fi
done
if [[ -L $implementation_source ]]; then
  fail 1 "profile implementation source is a symlink: templates/profiles/$profile/recipe/$profile"
fi
if [[ ! -d $implementation_source ]]; then
  fail 1 "profile template is incomplete: templates/profiles/$profile/recipe/$profile"
fi
template_recipe_resolved=$(CDPATH='' cd -- "$template/recipe" && pwd)
implementation_resolved=$(CDPATH='' cd -- "$implementation_source" && pwd)
expected_implementation="$template_recipe_resolved/$profile"
if [[ $implementation_resolved != "$expected_implementation" ]]; then
  fail 1 "profile implementation source resolves outside profile template: templates/profiles/$profile/recipe/$profile"
fi

run_tree_validation "$implementation_source" source

destination="$recipe_root/$profile"
relative_destination=${destination#"$recipe_root"/}
current="$recipe_root"
IFS=/ read -r -a destination_parts <<< "$relative_destination"
for part in "${destination_parts[@]}"; do
  current="$current/$part"
  if [[ -L $current ]]; then
    fail 1 "refusing to write through symlink: ${current#"$recipe_root"/}"
  fi
  if [[ $current != "$destination" && -e $current && ! -d $current ]]; then
    fail 1 "implementation destination path is blocked by a file: recipes/$slug/${current#"$recipe_root"/}"
  fi
done

if [[ -e $destination || -L $destination ]]; then
  fail 1 "implementation destination already exists: recipes/$slug/$profile"
fi

IFS=- read -r -a words <<< "$slug"
title=""
class_name=""
for word in "${words[@]}"; do
  first=$(printf '%s' "${word%"${word#?}"}" | tr '[:lower:]' '[:upper:]')
  capitalized="$first${word#?}"
  title+="$capitalized "
  class_name+="$capitalized"
done
title=${title% }

render() {
  LC_ALL=C sed -e "s/__RECIPE_SLUG__/$slug/g" \
    -e "s/__RECIPE_TITLE__/$title/g" \
    -e "s/__RECIPE_CLASS__/$class_name/g" \
    "$1"
}

profile_staging_name=${profile//\//-}
staging="$recipe_root/.add-implementation-$profile_staging_name.$$"
mkdir -- "$staging"
trap cleanup_failure EXIT

cp -R -- "$implementation_source/." "$staging/"
while IFS= read -r -d '' file; do
  render "$file" > "$file.tmp"
  if [[ -x $file ]]; then
    chmod +x "$file.tmp"
  fi
  mv -- "$file.tmp" "$file"
done < <(find "$staging" -type f -print0)

marker='__[A-Z][A-Z0-9_]*__'
if unresolved=$(LC_ALL=C grep -rlE "$marker" "$staging"); then
  fail 1 "unresolved template placeholders in: ${unresolved//"$staging"\//}"
fi
if unresolved=$(find "$staging" -name "*__[A-Z]*__*" | head -n 1) && [[ -n $unresolved ]]; then
  fail 1 "unresolved template placeholder in path: ${unresolved#"$staging"/}"
fi

run_tree_validation "$staging" staged

if [[ -e $destination || -L $destination ]]; then
  fail 1 "implementation destination already exists: recipes/$slug/$profile"
fi

created_parent_dirs=()
current="$recipe_root"
for part in "${destination_parts[@]}"; do
  current="$current/$part"
  if [[ $current == "$destination" ]]; then
    break
  fi
  if [[ ! -e $current ]]; then
    created_parent_dirs+=("$current")
  fi
done

mkdir -p -- "${destination%/*}"
if ! mv -- "$staging" "$destination"; then
  fail 1 "failed to install staged implementation at recipes/$slug/$profile"
fi
staging=""
created_parent_dirs=()
trap - EXIT

echo "added recipes/$slug/$profile from profile $profile"
echo
render "$template/next-steps.txt"
