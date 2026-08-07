# Releases

## How a version is decided

release-please reads the Conventional Commit history on `develop` and maintains a
release pull request there containing the version bump and the changelog entry. It
never writes to `main`, which is why the branch rule needs no exception.

## The sequence

```
1. Work merges into develop.        ghcr:develop, ghcr:sha-<short>,
                                    prerelease 0.x.y-develop.<run>
2. release-please opens a release PR into develop.
3. Merging it bumps the version and updates CHANGELOG.md on develop.
4. develop merges into main as a merge commit.
5. main tags vX.Y.Z, builds amd64 and arm64,
   pushes ghcr:X.Y.Z, ghcr:X.Y and ghcr:latest,
   and publishes the GitHub release.
```

Steps 4 and 5 are the only way a stable release happens. There is no manual tagging.

## Milestones

One minor release per milestone, v0.1 through v1.0. v1.0 is also when the repository
and the container images become public.

## Prereleases

Every merge into `develop` produces a development image and a prerelease. These are
for the two of us and for anyone who explicitly opts into `:develop`. They are not
announced and carry no compatibility promise.
