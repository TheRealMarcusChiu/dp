# Test fixtures: provenance

**Every file here is a hand-written SAMPLE, not output captured from a real tool run.**
They follow the formats documented by each tool (sources below) and exist to
exercise dp's parsers on shapes that matter: shared subtrees, cycles, multiple
versions, scopes, failures. Passing tests against them proves the parsers handle
these samples; it does **not** prove compatibility with any specific tool version.

Live-tool coverage is separate: `RealNpmTest` (npm) and `RealUvTest` (uv) in
`tests/test_dp.py` run the real tools in temporary scratch projects.

| File | Imitates | Format source |
|---|---|---|
| `npm-ls-problems.json` | `npm ls --all --json` with an invalid and a missing dependency, two versions of `ms`, a cycle | https://docs.npmjs.com/cli/v11/commands/npm-ls ; field names (`missing`, `required`, `invalid`, `problems`) match what npm 10 prints |
| `pnpm-list.json` | `pnpm list --json --depth Infinity`, one project, an aliased dependency | https://pnpm.io/cli/list (field names not formally versioned upstream) |
| `mvn-tree.txt` | `mvn dependency:tree -DoutputType=text -DappendOutput=true -DoutputFile=…` for a 3-module reactor, with a classifier and an optional dependency | https://maven.apache.org/plugins/maven-dependency-plugin/tree-mojo.html |
| `gradle-dependencies.txt` | `gradle -q dependencies` with `->`, `(*)`, `(c)`, `(n)`, `FAILED` | https://docs.gradle.org/current/userguide/viewing_debugging_dependencies.html |
| `uv-tree.txt` | `uv tree` with an extra, a dependency group and a `(*)` repeat | uv 0.11 `uv tree --help`; text layout as printed by uv |
| `uv-pip-tree.txt` | `uv pip tree` of an environment | uv 0.11 `uv pip tree --help` |
| `dotnet/project.assets.json` | NuGet restore output with two frameworks (`net8.0`, `net48`), a project reference, per-framework versions | https://learn.microsoft.com/en-us/nuget/concepts/dependency-resolution ; trimmed to the keys dp reads |
| `cargo-metadata.json` | `cargo metadata --format-version 1` for a virtual workspace (`resolve.root` null) with build/dev/target-specific edges | https://doc.rust-lang.org/cargo/commands/cargo-metadata.html |
| `go-mod-graph.txt` | `go mod graph` with two versions of one module | https://go.dev/ref/mod#go-mod-graph |
| `composer-show-tree.json` | `composer show --tree --format=json` | composer `ShowCommand.php` (generatePackageTree/addTree); the child `installed` key is taken from research notes and was not checked against a live Composer |
