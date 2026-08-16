"""Worker test package.

A package rather than loose modules, matching `tests/api` and `tests/core`:
without it pytest puts this directory on `sys.path` instead of the repository
root, and the shared fixtures in `tests.api.test_auth` cannot be imported.
"""
