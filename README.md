# Home School

Home tutoring platform for Uganda: parent and teacher mobile apps, an admin dashboard and a core service on Appwrite.

- Backlog: GitHub issues and the [project board](https://github.com/users/Matt-Murungi/projects/8).
- Decisions: [docs/adr/](docs/adr/).
- Work in progress and hand-off notes: [memory.md](memory.md).
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md); AI agents also read [CLAUDE.md](CLAUDE.md).

## Layout

| Path | Purpose | Status |
|---|---|---|
| `apps/parent` | Parent app (Flutter) | planned (F05) |
| `apps/teacher` | Teacher app (Flutter) | planned (F05) |
| `apps/admin` | Admin dashboard (Next.js) | planned (F06) |
| `packages/flutter_shared` | Shared Dart code | planned (F05) |
| `services/core` | Core service (FastAPI) | implemented (F03) |
| `appwrite/` | Appwrite config, migrations, seed | planned (F02) |
| `docs/` | ADRs (`docs/adr`), product docs placeholder (`docs/product`) | implemented |
| `scripts/` | Tooling and repo tests | implemented |
| `spikes/` | Throwaway spikes from D01/D02 | kept as is |

`packages.json` is the manifest; each package has a README with its purpose and test command.

## Commands

```sh
make test   # every implemented package; planned ones and missing tools are skipped with a message
make lint   # layout guard, secret scan, per-package lint
make help
```
