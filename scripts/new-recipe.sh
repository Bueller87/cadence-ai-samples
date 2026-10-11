#!/usr/bin/env bash
set -euo pipefail

languages="go java python"
classifiers="none systemone"
agents="none google-adk openai-agents"
implemented_profiles="python/bare"

# The single source of valid combinations. Help output and validation both use it.
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
usage: scripts/new-recipe.sh <recipe-slug> --language LANGUAGE --classifier CLASSIFIER --agent AGENT

Create recipes/<recipe-slug> from an implementation profile. All three choices
are required; there is no default profile.

  --language    ${languages// / | }
  --classifier  ${classifiers// / | }
  --agent       ${agents// / | }

Valid combinations:

$(print_matrix)

Implemented in this checkout: $implemented_profiles

Example:
  scripts/new-recipe.sh invoice-review --language python --classifier none --agent none
EOF
}

usage_error() {
  echo "new-recipe: $1" >&2
  echo >&2
  usage >&2
  exit 2
}

fail() {
  echo "new-recipe: $2" >&2
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
recipes_dir="$repo_root/recipes"
destination="$recipes_dir/$slug"

if [[ ! -d $template/recipe || ! -f $template/next-steps.txt ]]; then
  fail 1 "profile template is incomplete: templates/profiles/$profile"
fi
if [[ -e $destination || -L $destination ]]; then
  fail 1 "destination already exists: recipes/$slug"
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

# Build in a hidden sibling directory, then rename, so failures never leave a
# partial recipe at the destination.
mkdir -p -- "$recipes_dir"
staging="$recipes_dir/.new-recipe-$slug.$$"
mkdir -- "$staging"
trap 'rm -rf -- "$staging"' EXIT

cp -R -- "$template/recipe/." "$staging/"
find "$staging" \( -name .venv -o -name venv -o -name __pycache__ -o -name .pytest_cache \
  -o -name '*.egg-info' -o -name .DS_Store \) -prune -exec rm -rf -- {} +
while IFS= read -r -d '' file; do
  render "$file" > "$file.tmp"
  mv -- "$file.tmp" "$file"
done < <(find "$staging" -type f -print0)

marker='__[A-Z][A-Z0-9_]*__'
if unresolved=$(LC_ALL=C grep -rlE "$marker" "$staging"); then
  fail 1 "unresolved template placeholders in: ${unresolved//"$staging"\//}"
fi
if unresolved=$(find "$staging" -name "*__[A-Z]*__*" | head -n 1) && [[ -n $unresolved ]]; then
  fail 1 "unresolved template placeholder in path: ${unresolved#"$staging"/}"
fi

if [[ -e $destination || -L $destination ]]; then
  fail 1 "destination already exists: recipes/$slug"
fi
mv -- "$staging" "$destination"
trap - EXIT

echo "created recipes/$slug from profile $profile"
echo
render "$template/next-steps.txt"
