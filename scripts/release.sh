#!/usr/bin/env bash
#
# Cut a release from the version in pyproject.toml.
#
# Reads `version` from pyproject.toml, tags the current commit as `v<version>`
# (annotated), pushes the tag — which triggers .github/workflows/release.yml
# (on: push: tags: v*) to build and publish the multi-arch image — and creates
# the matching GitHub release with auto-generated notes.
#
# Idempotent: an existing tag/release is reused, not overwritten. Run after you
# have bumped the version in pyproject.toml and committed it.
#
# Usage:  scripts/release.sh
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# 1. Read the version from pyproject.toml.
version="$(grep -m1 -E '^version = ' pyproject.toml | sed -E 's/^version = "(.+)"/\1/')"
if [ -z "${version}" ]; then
  echo "ERROR: could not read 'version' from pyproject.toml" >&2
  exit 1
fi
tag="v${version}"
echo "Version ${version}  ->  tag ${tag}"

# 2. Require a clean working tree so the tag reflects committed state.
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: working tree is dirty — commit or stash changes before releasing." >&2
  exit 1
fi

# 3. Create the annotated tag at HEAD if it doesn't exist yet.
if git rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
  existing="$(git rev-parse --short "${tag}^{commit}")"
  head="$(git rev-parse --short HEAD)"
  if [ "${existing}" != "${head}" ]; then
    echo "NOTE: tag ${tag} already exists at ${existing} (HEAD is ${head}); using the existing tag." >&2
  else
    echo "Tag ${tag} already exists at HEAD (${head})."
  fi
else
  git tag -a "${tag}" -m "Release ${tag}"
  echo "Created tag ${tag} at $(git rev-parse --short HEAD)."
fi

# 4. Push the tag (triggers the Release workflow).
git push origin "refs/tags/${tag}"

# 5. Publish the GitHub release (skip if it already exists).
if gh release view "${tag}" >/dev/null 2>&1; then
  echo "GitHub release ${tag} already exists — leaving it as-is."
else
  gh release create "${tag}" --verify-tag --title "${tag}" --generate-notes
  echo "Published GitHub release ${tag}."
fi
