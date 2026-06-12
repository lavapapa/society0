# Release Security Checklist

Use this checklist before publishing Society0 as a public repository or package.

## Required Before Public Release

- Run a current-tree secret scan.
- Run a git-history secret scan.
- Confirm no local run artifacts, edit histories, checkpoints, Chroma stores, logs, `.env` files, or database files are tracked.
- Confirm examples and skill docs use placeholders only.
- Build the source distribution and inspect its file list before upload.

## Known Blocker In This Working Copy

The current tree has been cleaned, but git history still contains a real-looking API key in the initial import commit. Do not publish this repository history until the key has been revoked and the history has either been rewritten or replaced by a fresh clean import.

Recommended public-release path:

1. Revoke or rotate the exposed key with the provider.
2. Create a fresh public repository from the cleaned current tree, or rewrite history with a tool such as `git filter-repo`.
3. Re-run the current-tree and git-history secret scans.
4. Build and inspect the package artifact.
