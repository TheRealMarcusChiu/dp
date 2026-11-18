# dp — one dependency tree view for many package managers

`dp` runs the dependency tool **already installed on your PATH** (npm, mvn, gradle,
uv, cargo, …), turns its output into one small graph, and prints it the same way
for every ecosystem. Example (rendered from the npm sample in `tests/fixtures/`):

```
$ dp npm
app 1.0.0
├── @scope/util 2.0.0
│   └── ms 2.1.3
├── debug 2.6.9 [invalid]
│   └── ms 2.0.0
├── left-pad (required ^1.3.0) [missing]
└── cyc-a 1.0.0
    └── cyc-b 1.0.0
        └── cyc-a 1.0.0 (cycle)
```

dp only chooses inspection commands: it never issues install, restore, lock, update
or fetch commands itself. The native tools it runs still evaluate your build files and
may resolve, download into caches or write metadata — see [Safety](#safety). It is a single
Python file using only the standard library.

## Requirements and install

- **Python 3.8 or newer** (`python3 --version`). Tested only with Python 3.11 on Linux.
  macOS: the Xcode Command Line Tools `python3` (3.9) or a Homebrew/python.org Python works.
- The native tool for the project you inspect, on `PATH`.
- `dp pip` / `dp pip3` additionally need [`uv`](https://docs.astral.sh/uv/) on `PATH`.

```sh
chmod +x dp
mkdir -p ~/.local/bin && cp dp ~/.local/bin/dp     # any directory on your PATH
dp --help
```

**Not tested on a real Mac.** Everything was developed and tested on Linux; dp uses
nothing Linux-specific, but no macOS run has been done.

## Usage

```
dp [TOOL] [-C DIRECTORY] [--format tree|json|native] [--depth N | --direct] [--ascii] [-- NATIVE_ARGS...]
dp --diff BEFORE.json AFTER.json
```

| Option | Meaning |
|---|---|
| `TOOL` | Which manager to use. Omit it and dp looks at the files in DIRECTORY (not subdirectories). |
| `-C DIRECTORY` | Project directory (default: current directory). |
| `--format tree` | Default. Unicode tree, no color. |
| `--format json` | Full graph snapshot (see below). Never truncated. |
| `--format native` | Runs the native command and passes its output and exit status through untouched. |
| `--depth N` | Tree only: show N levels below the roots. `0` = roots only. |
| `--direct` | Tree only: same as `--depth 1`, i.e. each root plus its direct dependencies. |
| `--ascii` | Tree only: `+--`, `\--`, `|` connectors for terminals without Unicode. |
| `--diff A B` | Compare two dp JSON snapshots. Offline; no project or tool needed. |
| `-- NATIVE_ARGS` | Appended as separate arguments to the native command (never through a shell). |

`--depth`, `--direct` and `--ascii` with `--format json` or `native` are rejected,
not ignored. For a native depth limit, pass the tool's own flag after `--`.

Examples:

```sh
dp                                  # detect the project in the current directory
dp mvn -C "my service" --direct     # Maven project in a directory with a space
dp gradle -- --configuration runtimeClasspath
dp npm --format native              # exactly what `npm ls --all` prints
dp pip3                             # environment managed by the pip3 on PATH
dp npm --format json > before.json
# ...upgrade something...
dp npm --format json > after.json
dp --diff before.json after.json
```

### Exit status

| Code | Meaning |
|---|---|
| 0 | Tree printed / snapshots identical |
| 1 | Tree printed but the tool reported missing, invalid, extraneous or FAILED dependencies / snapshots differ |
| 2 | Usage error, tool missing, tool failed, unsupported output, invalid snapshot |
| other | `--format native`: the native tool's own exit status |

On failure dp prints the exact native command, its exit code and its error output.

## Backends

| TOOL (aliases) | Normalized tree/json from | Native command | Roots |
|---|---|---|---|
| `npm` | `npm ls --all --json` | `npm ls --all` | the package |
| `pnpm` | `pnpm list --json --depth Infinity` | `pnpm list --depth Infinity` | each listed project (dev/optional labelled) |
| `mvn` (`maven`) | `mvn -o -B dependency:tree -DoutputType=text -DappendOutput=true -DoutputFile=<temp file>` | `mvn -o -B dependency:tree` | each reactor module |
| `gradle` | `gradle --offline --console=plain -q dependencies` | same | each configuration of the project in DIRECTORY |
| `uv` | `uv tree --frozen --color never --no-python-downloads` (needs an existing `uv.lock`) | same | the project / workspace members |
| `pip`, `pip3` | `<pip> debug` to find its interpreter, then `uv pip tree --python <it> --color never --no-python-downloads` | the `uv pip tree` command | synthetic `environment` |
| `dotnet` (`nuget`) | reads `obj/project.assets.json` (no command) | SDK from `dotnet --version`: 10+ `dotnet package list --project <proj> --include-transitive --no-restore`, older `dotnet list <proj> package --include-transitive` | the project, once per target framework/RID |
| `cargo` | `cargo metadata --format-version 1 --frozen` | `cargo tree --frozen` | each workspace member |
| `go` (`golang`) | `go mod graph` with `GOTOOLCHAIN=local` (may still download module metadata, see below) | same | the main module(s) |
| `composer` | `composer --no-plugins --no-scripts --no-interaction show --tree --format=json` | same without `--format=json` | synthetic `project` |
| `yarn` | — native only | `yarn list` (Yarn 1) / `yarn info --all --recursive` (when `.yarnrc.yml` exists) | |
| `bun` | — native only | `bun pm ls --all` (a flat list) | |
| `bundle` (`bundler`) | — native only | `bundle list` (a flat list) with `BUNDLE_FROZEN=true` | |
| `swift` (`swiftpm`) | — native only | `swift package --force-resolved-versions --skip-update show-dependencies --format text` | |
| `poetry` | — native only | `poetry show --tree` | |

Native-only tools refuse `--format tree/json` with an error: their machine output is
flat, unversioned, or was not verified, and dp will not invent edges.
CocoaPods has no dependency-tree command and is not supported.

### Autodetection

dp checks only the given directory for: `pom.xml`, `build.gradle(.kts)`,
`settings.gradle(.kts)`, `uv.lock`, `poetry.lock`, `Cargo.toml`, `go.mod`,
`composer.json`, `Package.swift`, `Gemfile`, `*.csproj/*.fsproj/*.vbproj/*.sln/*.slnx`,
and for JavaScript a lockfile (`package-lock.json`, `npm-shrinkwrap.json`,
`node_modules/.package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lock(b)`) or the
`packageManager` field of `package.json`. If more than one tool matches (e.g. a Maven
project with a `package.json`), or a `package.json` has no lockfile/packageManager, dp
stops and prints the exact `dp TOOL -C DIR` commands to choose from. Python
environments are never autodetected: say `dp pip`, `dp pip3` or `dp uv`.

## What each tree means (read this before trusting a tree)

- **npm / pnpm**: what is installed in `node_modules`. Missing/invalid/extraneous
  entries stay in the tree, marked, and dp exits 1.
- **Maven**: Maven's conflict-resolved tree per module. Edge labels are scope (plus
  `optional`). Type and classifier are part of the name when not a plain jar.
  `-Dverbose` output is rejected (omitted nodes are not real dependencies).
  Dependencies are scoped to their module (JSON context = the module root id, e.g.
  `[com.example:app@1.0.0]`), because modules can resolve the same artifact to different
  children. Changing a module's version therefore changes its dependencies' ids too.
- **Gradle**: one root per configuration; packages are scoped to their configuration
  (`[runtimeClasspath]` in JSON ids). `requested 1.7.30` marks a version chosen by
  conflict resolution, `constraint` a `(c)` constraint (not a dependency), `not
  resolved` a `(n)` declared-only entry. `FAILED` becomes status `failed` and exit 1.
  Gradle's `(*)` repeats keep the children from the first occurrence.
  Only the project in DIRECTORY is reported: run dp in each subproject directory
  (Gradle runs that subproject's `dependencies` task).
- **uv**: the resolution stored in the existing `uv.lock` (`--frozen`: no locking, no
  freshness check, so it can be stale). No `uv.lock` → dp tells you to run `uv lock`.
- **pip / pip3**: the installed environment of *that* pip (an active virtualenv's pip
  comes first on PATH, so it is honoured). The interpreter comes from `pip debug`,
  whose output pip documents as experimental; dp warns about this. Rendering uses
  `uv pip tree`, which evaluates markers and extras. Top-level packages are the
  ones nothing else requires — not necessarily what you asked to install.
- **dotnet**: NuGet's restored graph from `obj/project.assets.json`, one root per target
  framework (and RID), versions per framework. dp does not restore: a missing assets
  file or one older than the project file is an error telling you to run
  `dotnet restore`. Freshness is checked against the project file only, not imported
  files such as `Directory.Packages.props` or `NuGet.config`; dp prints a warning saying so.
  `--format native` picks the command for the SDK that `dotnet --version` reports in
  that directory (so `global.json` applies), passing `--no-restore` on SDK 10+, where the
  command would otherwise restore implicitly. An unrecognised version string is an error. `packages.config` projects are refused (that file is a flat list;
  a tree cannot be reconstructed). The directory must contain exactly one project file;
  for a solution, run dp in each project directory. A relocated
  `MSBuildProjectExtensionsPath` is not supported.
- **cargo**: the resolved graph for all platforms; edge labels show `build`, `dev`
  and target `cfg(...)`. Every package, workspace members included, has Cargo's opaque
  package id (source + version, e.g. a `registry+…`, `git+…` or `path+file://…` id) as its
  context. The same name@version from two sources stays two nodes, and a source switch shows
  up in `--diff` as one package removed and one added (not as `~`). Roots show their path id.
- **go**: the module *requirement* graph from `go mod graph`, not the MVS-selected build
  list: a module can appear at several versions. dp forces `GOTOOLCHAIN=local` (overriding
  your setting) so go does not switch to or download another toolchain. That is the only
  guard: `go mod graph` can still download missing module metadata through your `GOPROXY`,
  fill the module cache and update `go.sum`, and your `GOFLAGS` still apply. dp does
  not disable checksum verification and never runs `go mod tidy`/`download` or `go get`.
- **composer**: the root requirements and their requirements. Edge labels are version
  constraints; a child's version is shown only when Composer reports it as installed.

## JSON snapshot (`dp.graph/1`)

```json
{
  "schema": "dp.graph/1",
  "tool": "npm",
  "semantics": "installed node_modules dependency tree (npm ls --all)",
  "roots": ["app@1.0.0"],
  "nodes": [{"id": "debug@2.6.9 (invalid)", "name": "debug", "version": "2.6.9", "status": "invalid"}, "..."],
  "edges": [{"from": "app@1.0.0", "to": "debug@2.6.9 (invalid)"}, {"from": "app@1.0.0", "to": "left-pad (missing)", "label": "required ^1.3.0"}],
  "warnings": ["missing: left-pad@^1.3.0, required by app@1.0.0"],
  "provenance": {"command": ["npm", "ls", "--all", "--json"], "directory": "/path/to/app"}
}
```

- Node `id` = `[kind:]name[@version][ [context]][ (status)]`. Optional fields: `version`,
  `context` (Gradle configuration, .NET target, Maven module root id, Cargo opaque package id),
  `status` (`missing`, `invalid`, `extraneous`, `failed`), `kind` (synthetic roots:
  `configuration`, `environment`, `project`).
- Each package version is its own node, so two versions of one package are never merged.
- Apart from Cargo, a package is identified by name and version, not by source: replacing a
  registry package with a git or local copy of the same version is not reported by `--diff`.
- Edges keep every parent→child link, including shared dependencies and cycles; the tree
  view expands a shared subtree once and marks later occurrences `(*)` and back-edges `(cycle)`.
- Nodes and edges are sorted, so the same graph gives the same file.

`dp --diff` validates both files first. `id` and `name` must be non-empty strings.
`version`, `context`, `status`, `kind` and an edge's `label` may be absent, `null` or a
string; `null` counts as absent. Every edge end and every root must be a node id.
Anything else exits 2 with the offending node or edge.

`dp --diff` compares only `nodes`, `roots` and `edges`. `tool`, `semantics`, `warnings` and
`provenance` (paths, commands) are ignored. Output lines:

```
~ ms: 2.0.0 -> 3.0.0          version change of the same package (and context)
+ new@1 / - old@1             package added / removed
+ edge app@1 -> new@1         parent-child link added (removed: -)
+ root ... / - root ...       roots changed
```

## Safety

What dp does:

- Commands run as argument lists, never through a shell.
- dp chooses inspection commands only. It never issues install, restore, lock, update,
  tidy or fetch commands, and never runs wrappers (`./mvnw`, `./gradlew`).
- Guards it adds: Maven `-o` and Gradle `--offline`; uv `--frozen`, cargo `--frozen`;
  swift `--force-resolved-versions --skip-update` (fails rather than re-resolving
  `Package.resolved`); .NET SDK 10+ `--no-restore`. Child environment, overriding
  inherited values: `GOTOOLCHAIN=local`, `BUNDLE_FROZEN=true` (bundler may not relock),
  `COREPACK_ENABLE_NETWORK=0` (Corepack `pnpm`/`yarn` shims may not download a package manager).

What dp cannot promise:

- The native tools still evaluate project build files, plugins, Gemfiles and Swift
  manifests, and may resolve dependencies, download into caches, or write metadata and
  report files. Go may fetch module metadata and update `go.sum`. Swift may still fetch
  missing checkouts and populate `.build`. Version managers (asdf, rbenv, mise, …) and
  other shims on PATH can bootstrap themselves. None of this is a sandbox, and dp's tests
  check the argv and environment it passes, not the absence of side effects.
- Arguments after `--` go to the native tool unchanged and can deliberately change what it does.
- **Only run dp on projects you trust.**

## Limitations

- Not run on macOS. Live-tested only with npm 10.9.8 and uv 0.11.16 on Linux.
  Maven, Gradle, .NET, pnpm, Cargo, Go and Composer support is tested against
  hand-written sample outputs (`tests/fixtures/`), not against those tools.
- Maven needs its dependency plugin and your dependencies already in the local
  repository (dp runs it with `-o`); run your normal build once first.
- Gradle: only the project in DIRECTORY; the buildscript classpath (`buildEnvironment`) is not shown.
- `--depth` limits the tree view only; there is no depth filter on JSON.
- No color, no Windows support, no recursive monorepo scan.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Fake tools (small scripts in a temporary PATH) check exact argv, working directory, exit
codes and error handling. Parsers are tested on the sample fixtures. Two tests run real
tools when they are installed: npm on a scratch project of local `file:` packages
(offline, `--ignore-scripts`), and uv on a scratch empty venv and a scratch lockfile with no
dependencies.
