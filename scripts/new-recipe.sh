#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <recipe-slug>" >&2
  exit 2
fi

slug=$1
if [[ ! $slug =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)*$ ]]; then
  echo "recipe slug must use lowercase letters, numbers, and single hyphens" >&2
  exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
template="$repo_root/templates/recipe"
destination="$repo_root/recipes/$slug"

if [[ -e $destination ]]; then
  echo "destination already exists: $destination" >&2
  exit 1
fi

IFS=- read -r -a words <<< "$slug"
title=""
class_name=""
for word in "${words[@]}"; do
  title+="${word^} "
  class_name+="${word^}"
done
title=${title% }
python_package=${slug//-/_}

cp -R -- "$template" "$destination"
while IFS= read -r -d '' file; do
  sed -e "s/__RECIPE_SLUG__/$slug/g" \
      -e "s/__RECIPE_TITLE__/$title/g" \
      -e "s/__RECIPE_CLASS__/$class_name/g" \
      -e "s/__PYTHON_PACKAGE__/$python_package/g" \
      "$file" > "$file.tmp"
  mv -- "$file.tmp" "$file"
done < <(find "$destination" -type f -print0)

echo "created recipes/$slug"
echo "next: cd recipes/$slug/python"
