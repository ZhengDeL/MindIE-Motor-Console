# Repository instructions

- For every new update or task, create a new branch before editing. Use a descriptive name such as `feat/YYYYMMDD-topic`, `fix/YYYYMMDD-topic`, or `docs/YYYYMMDD-topic`.
- Continue commits belonging to the same active update on its branch. Start the next update on a new branch; do not reuse a previous delivery branch.
- Task branches are local by default. Unless explicitly requested, do not publish temporary development branches to the canonical repository, `https://github.com/ZhengDeL/MindIE-Motor-Console`. Its only public code branch is `main`; the initial product release is `v0.11.0`. Create fixed tags only for releases actually published in this repository.
- Review and validate work on its task branch, then obtain explicit authorization before integrating it into `main` or publishing a release. External contributors use branches in their own forks and open pull requests against canonical `main`; canonical temporary branches are not long-term public entry points.
- Do not commit or push implementation changes directly to `main` or `master`. Do not force-push or merge without an explicit request.
- Testing or branch cleanup never authorizes direct implementation edits on `main`, a force-push, or an unapproved merge. Remote temporary-branch removal requires a checked backup and explicit authorization; it does not authorize rewriting commit history or moving release tags.
- Inspect the current branch and working tree first. Preserve existing uncommitted work and obtain a suitable base without resetting or overwriting it.
- Keep `mindie-motor/upstream` pinned to its recorded submodule commit. Read the relevant native source before adapting Motor behavior; do not silently update upstream.
- Preserve arbitrary original Motor JSON fields. Explicitly reject unsupported runtime operations; do not report simulated behavior as a real deployment result.
- Never commit credentials, kubeconfig, runtime databases, private operational records, node logs, or local environment files. Use documentation example addresses in fixtures and shared documentation.
- Run checks appropriate to the change. Local mocks, browser demo acceptance, container builds, and real NPU/Kubernetes acceptance are distinct evidence.
- Keep all tests, test configuration, browser acceptance scripts and generated test artifacts local. Do not commit or push them. Inspect the final Git tree before publishing; existing tracked test files must be removed from the index while retaining local copies.
- If NeuroTrace tools are available, check their status and consult file-scoped context before changing known modules. Save durable context only when authorized.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the branch workflow.
