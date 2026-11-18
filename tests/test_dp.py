"""Tests for dp. Run from the repo root: python3 -m unittest discover -s tests -v

Fixture-backed tests prove parsing of SAMPLE output only (see fixtures/README.md).
Live-tool tests (RealNpmTest, RealUvTest) skip when npm/uv are not on PATH.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DP = os.path.join(os.path.dirname(HERE), 'dp')
FIX = os.path.join(HERE, 'fixtures')

_loader = importlib.machinery.SourceFileLoader('dp', DP)
dp = importlib.util.module_from_spec(importlib.util.spec_from_loader('dp', _loader))
_loader.exec_module(dp)


def fixture(name):
    with open(os.path.join(FIX, name), encoding='utf-8') as f:
        return f.read()


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content if isinstance(content, str) else json.dumps(content))


def edges(snapshot):
    return {(e['from'], e['to'], e.get('label')) for e in snapshot['edges']}


class Scratch(unittest.TestCase):
    """Temp project dir (space + non-ASCII in its path) and a private bin dir used as PATH."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.proj = os.path.join(self.tmp, 'my projé')
        self.bin = os.path.join(self.tmp, 'bin')
        os.makedirs(self.proj)
        os.makedirs(self.bin)

    def fake(self, name, stdout='', code=0, stderr='', body=''):
        """Fake native tool: logs argv/cwd to NAME.log, runs BODY, prints STDOUT/STDERR, exits CODE."""
        path = os.path.join(self.bin, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('#!%s\nimport json, os, sys\n'
                    'with open(%r, "w") as log: json.dump({"argv": sys.argv[1:], "cwd": os.getcwd()}, log)\n'
                    '%s\nsys.stdout.write(%r)\nsys.stderr.write(%r)\nsys.exit(%d)\n'
                    % (sys.executable, path + '.log', body, stdout, stderr, code))
        os.chmod(path, 0o755)

    def log(self, name):
        with open(os.path.join(self.bin, name + '.log'), encoding='utf-8') as f:
            return json.load(f)

    def dp(self, *args, path=None, **env):
        env = dict(os.environ, PATH=self.bin if path is None else path, PYTHONIOENCODING='utf-8', **env)
        return subprocess.run([sys.executable, DP] + list(args), cwd=self.proj, env=env,
                              capture_output=True, text=True, encoding='utf-8')


NPM_TREE = '''\
app 1.0.0
├── @scope/util 2.0.0
│   └── ms 2.1.3
├── debug 2.6.9 [invalid]
│   └── ms 2.0.0
├── left-pad (required ^1.3.0) [missing]
└── cyc-a 1.0.0
    └── cyc-b 1.0.0
        └── cyc-a 1.0.0 (cycle)'''


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.g = dp.parse_npm(json.loads(fixture('npm-ls-problems.json')))

    def test_npm_tree_keeps_problems_versions_and_cycles(self):
        self.assertEqual(dp.render(self.g), NPM_TREE)
        self.assertTrue(self.g.problem)
        self.assertIn('missing: left-pad@^1.3.0, required by app@1.0.0', self.g.warnings)

    def test_ascii_and_direct(self):
        self.assertEqual(dp.render(self.g, depth=1, ascii=True), '''\
app 1.0.0
+-- @scope/util 2.0.0
+-- debug 2.6.9 [invalid]
+-- left-pad (required ^1.3.0) [missing]
\\-- cyc-a 1.0.0''')
        self.assertEqual(dp.render(self.g, depth=0), 'app 1.0.0')

    def test_shared_subtree_rendered_once_but_all_edges_kept(self):
        g = dp.parse_npm({'name': 'r', 'version': '1', 'dependencies': {
            'a': {'version': '1', 'dependencies': {'c': {'version': '1', 'dependencies': {'d': {'version': '1'}}}}},
            'b': {'version': '1', 'dependencies': {'c': {'version': '1'}}}}})
        self.assertEqual(dp.render(g), '''\
r 1
├── a 1
│   └── c 1
│       └── d 1
└── b 1
    └── c 1 (*)''')
        self.assertEqual(edges(g.snapshot({})), {
            ('r@1', 'a@1', None), ('r@1', 'b@1', None), ('a@1', 'c@1', None),
            ('b@1', 'c@1', None), ('c@1', 'd@1', None)})


class NpmCliTest(Scratch):
    def test_problems_exit_1_and_native_args_forwarded(self):
        self.fake('npm', fixture('npm-ls-problems.json'), code=1, stderr='npm error code ELSPROBLEMS\n')
        r = self.dp('npm', '--', '--omit=dev')
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout, NPM_TREE + '\n')
        self.assertIn('ELSPROBLEMS', r.stderr)
        self.assertEqual(self.log('npm')['argv'], ['ls', '--all', '--json', '--omit=dev'])
        self.assertEqual(os.path.realpath(self.log('npm')['cwd']), os.path.realpath(self.proj))

    def test_json_snapshot(self):
        self.fake('npm', fixture('npm-ls-problems.json'), code=1)
        r = self.dp('npm', '--format', 'json')
        self.assertEqual(r.returncode, 1, r.stderr)
        snap = json.loads(r.stdout)
        self.assertEqual(snap['schema'], 'dp.graph/1')
        self.assertEqual(snap['roots'], ['app@1.0.0'])
        self.assertIn(('debug@2.6.9 (invalid)', 'ms@2.0.0', None), edges(snap))
        self.assertIn(('app@1.0.0', 'left-pad (missing)', 'required ^1.3.0'), edges(snap))
        ids = [n['id'] for n in snap['nodes']]
        self.assertEqual(ids, sorted(ids))

    def test_nonzero_exit_without_dependency_problems_is_an_error(self):
        self.fake('npm', '{"error": {"code": "EJSONPARSE"}}', code=1, stderr='npm error bad package.json\n')
        r = self.dp('npm')
        self.assertEqual(r.returncode, 2)
        self.assertIn('command failed (exit 1): npm ls --all --json', r.stderr)
        self.assertIn('npm error bad package.json', r.stderr)

    def test_malformed_output(self):
        self.fake('npm', 'garbage')
        r = self.dp('npm')
        self.assertEqual(r.returncode, 2)
        self.assertIn('not JSON', r.stderr)

    def test_missing_tool_is_not_substituted(self):
        r = self.dp('npm')
        self.assertEqual(r.returncode, 2)
        self.assertIn('npm not found on PATH', r.stderr)

    def test_native_runs_real_command_and_passes_through(self):
        self.fake('npm', 'native npm tree\n', code=3)
        r = self.dp('npm', '--format', 'native', '--', '--depth=0')
        self.assertEqual((r.returncode, r.stdout), (3, 'native npm tree\n'))
        self.assertEqual(self.log('npm')['argv'], ['ls', '--all', '--depth=0'])

    def test_incompatible_or_unknown_options_rejected(self):
        for args in (['npm', '--format', 'json', '--depth', '1'], ['npm', '--format', 'native', '--ascii'],
                     ['npm', '--depth', '-1'], ['npm', '--depth', '1', '--direct'], ['npm', '--bogus'],
                     ['npm', '-C', 'no such dir'], ['--diff', 'a.json', 'b.json', 'npm'], ['nosuchtool']):
            r = self.dp(*args)
            self.assertEqual(r.returncode, 2, args)

    def test_help(self):
        r = self.dp('--help')
        self.assertEqual(r.returncode, 0)
        for word in ('usage: dp', '--diff', '--format', 'native', 'gradle', 'dotnet'):
            self.assertIn(word, r.stdout)


def snapshot(nodes, edge_list, roots=None, **extra):
    """Hand-built dp snapshot: nodes as (name, version) pairs, edges as (from_id, to_id[, label])."""
    snap = {'schema': 'dp.graph/1', 'tool': 'npm', 'semantics': 's',
            'nodes': [{'id': '%s@%s' % nv, 'name': nv[0], 'version': nv[1]} for nv in nodes],
            'edges': [dict({'from': e[0], 'to': e[1]}, **({'label': e[2]} if len(e) > 2 else {})) for e in edge_list],
            'roots': roots or ['app@1'], 'warnings': [], 'provenance': {}}
    snap.update(extra)
    return snap


class DiffTest(Scratch):
    BEFORE = snapshot([('app', '1'), ('ms', '2.0.0'), ('ms', '2.1.3'), ('debug', '2'), ('old', '1')],
                      [('app@1', 'debug@2'), ('debug@2', 'ms@2.0.0'), ('app@1', 'ms@2.1.3'), ('app@1', 'old@1')])

    def diff(self, before, after):
        write(os.path.join(self.tmp, 'b.json'), before)
        write(os.path.join(self.tmp, 'a.json'), after)
        return self.dp('--diff', os.path.join(self.tmp, 'b.json'), os.path.join(self.tmp, 'a.json'))

    def test_identical_graphs_ignore_order_and_provenance(self):
        after = dict(self.BEFORE, nodes=self.BEFORE['nodes'][::-1], edges=self.BEFORE['edges'][::-1],
                     provenance={'directory': '/elsewhere', 'command': ['npm']}, warnings=['w'])
        r = self.diff(self.BEFORE, after)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''))

    def test_versions_added_removed_and_edges(self):
        after = snapshot([('app', '1'), ('ms', '2.1.3'), ('ms', '3.0.0'), ('debug', '2'), ('new', '1')],
                         [('app@1', 'debug@2'), ('debug@2', 'ms@3.0.0'), ('app@1', 'ms@2.1.3', 'dev'),
                          ('app@1', 'new@1')])
        r = self.diff(self.BEFORE, after)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            '~ ms: 2.0.0 -> 3.0.0', '+ new@1', '- old@1',
            '- edge app@1 -> ms@2.1.3', '- edge app@1 -> old@1', '- edge debug@2 -> ms@2.0.0',
            '+ edge app@1 -> ms@2.1.3 (dev)', '+ edge app@1 -> new@1', '+ edge debug@2 -> ms@3.0.0'])

    def test_edge_only_change(self):
        after = dict(self.BEFORE, edges=self.BEFORE['edges'] + [{'from': 'old@1', 'to': 'ms@2.0.0'}])
        r = self.diff(self.BEFORE, after)
        self.assertEqual((r.returncode, r.stdout), (1, '+ edge old@1 -> ms@2.0.0\n'))

    def test_malformed_node_and_edge_fields_rejected_without_traceback(self):
        empty = dict(snapshot([], []), roots=[])
        cases = [('context', ['bad']), ('version', ['bad']), ('status', {'x': 1}), ('kind', 3),
                 ('name', ''), ('id', '')]
        for field, value in cases:
            bad = dict(snapshot([('x', '1')], []), roots=[])
            bad['nodes'][0][field] = value
            for pair in ((empty, bad), (bad, empty)):
                r = self.diff(*pair)
                self.assertEqual(r.returncode, 2, (field, r.stderr))
                self.assertIn('is not a dp.graph/1 snapshot', r.stderr)
                self.assertNotIn('Traceback', r.stderr)
        r = self.diff(self.BEFORE, dict(self.BEFORE, edges=[{'from': 'app@1', 'to': 'old@1', 'label': ['x']}]))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn('Traceback', r.stderr)
        # policy: optional fields may be absent or null
        nulls = dict(self.BEFORE, nodes=[dict(n, context=None, status=None) for n in self.BEFORE['nodes']],
                     edges=[dict(e, label=None) for e in self.BEFORE['edges']])
        self.assertEqual(self.diff(self.BEFORE, nulls).returncode, 0)

    def test_invalid_snapshots(self):
        bad = [dict(self.BEFORE, schema='other'), dict(self.BEFORE, nodes={}),
               dict(self.BEFORE, edges=[{'from': 'app@1', 'to': 'ghost@1'}]),
               dict(self.BEFORE, roots=['ghost@1']), dict(self.BEFORE, roots=[['x']]),
               dict(self.BEFORE, nodes=self.BEFORE['nodes'] * 2), 'not json {']
        for b in bad:
            r = self.diff(self.BEFORE, b)
            self.assertEqual(r.returncode, 2, b)
            self.assertIn('dp:', r.stderr)
        r = self.dp('--diff', 'missing-before.json', 'missing-after.json')
        self.assertEqual(r.returncode, 2)

    def test_cli_snapshot_round_trip(self):
        self.fake('npm', fixture('npm-ls-problems.json'), code=1)
        snap = self.dp('npm', '--format', 'json').stdout
        r = self.diff(snap, snap)
        self.assertEqual(r.returncode, 0, r.stderr)


class UvPipTest(Scratch):
    def test_parse_uv_tree_keeps_labels_and_children_of_repeated_nodes(self):
        g = dp.parse_uv(fixture('uv-tree.txt'), dp.Graph('uv', 's'))
        self.assertEqual(g.roots, ['demo@0.1.0'])
        self.assertTrue({('demo@0.1.0', 'rich[jupyter]@13.7.1', 'extra: cli'), ('demo@0.1.0', 'pytest@8.2.2', 'group: dev'),
                         ('pytest@8.2.2', 'anyio@4.4.0', None), ('anyio@4.4.0', 'idna@3.7', None)} <= set(g.edges))
        self.assertIn('    └── anyio 4.4.0 (*)', dp.render(g))
        self.assertRaises(dp.Fail, dp.parse_uv, 'demo v1\n  weird line\n', dp.Graph('uv', 's'))

    def test_uv_needs_existing_lock_and_never_locks(self):
        self.fake('uv')
        r = self.dp('uv')
        self.assertEqual(r.returncode, 2)
        self.assertIn('uv lock', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.bin, 'uv.log')))

    def test_uv_project_tree(self):
        write(os.path.join(self.proj, 'uv.lock'), '')
        self.fake('uv', fixture('uv-tree.txt'))
        r = self.dp('uv', '--direct')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, 'demo 0.1.0\n├── httpx 0.27.0\n├── rich[jupyter] 13.7.1 (extra: cli)\n'
                                   '└── pytest 8.2.2 (group: dev)\n')
        self.assertEqual(self.log('uv')['argv'], ['tree', '--frozen', '--color', 'never', '--no-python-downloads'])
        self.fake('uv', '')
        self.assertEqual(self.dp('uv').returncode, 2)

    def test_pip3_uses_the_interpreter_of_that_pip(self):
        self.fake('pip3', 'sys.version: 3.12.1\nsys.executable: /opt/py dir/bin/python3\n')
        self.fake('uv', fixture('uv-pip-tree.txt'))
        r = self.dp('pip3', '--format', 'json')
        self.assertEqual(r.returncode, 0, r.stderr)
        snap = json.loads(r.stdout)
        self.assertEqual(snap['roots'], ['environment:environment'])
        self.assertTrue({('environment:environment', 'requests@2.32.3', None), ('environment:environment', 'rich@13.7.1', None),
                         ('markdown-it-py@3.0.0', 'mdurl@0.1.2', None)} <= edges(snap))
        self.assertTrue(any('pip3 debug' in w for w in snap['warnings']))
        self.assertEqual(self.log('pip3')['argv'], ['debug'])
        self.assertEqual(self.log('uv')['argv'], ['pip', 'tree', '--python', '/opt/py dir/bin/python3',
                                                  '--color', 'never', '--no-python-downloads'])

    def test_pip_without_reported_interpreter_or_empty_environment(self):
        self.fake('pip', 'no executable here\n')
        self.fake('uv', '')
        r = self.dp('pip')
        self.assertEqual(r.returncode, 2)
        self.assertIn('sys.executable', r.stderr)
        self.fake('pip', 'sys.executable: /usr/bin/python3\n')
        r = self.dp('pip')
        self.assertEqual((r.returncode, r.stdout), (0, 'environment\n'))


class MavenTest(Scratch):
    def test_parse_modules_scopes_classifiers(self):
        g = dp.parse_mvn(fixture('mvn-tree.txt'))
        core, app = 'com.example:core@1.0.0', 'com.example:app@1.0.0'
        self.assertEqual(g.roots, ['com.example:parent:pom@1.0.0', core, app])
        self.assertTrue({(app, 'com.example:core@1.0.0 [%s]' % app, 'compile'),
                         (core, 'org.lwjgl:lwjgl:jar:natives-macos@3.3.1 [%s]' % core, 'runtime'),
                         ('com.example:core@1.0.0 [%s]' % app, 'org.lwjgl:lwjgl:jar:natives-macos@3.3.1 [%s]' % app, 'runtime'),
                         (app, 'com.fasterxml.jackson.core:jackson-databind@2.15.0 [%s]' % app, 'compile, optional'),
                         ('junit:junit@4.13.2 [%s]' % core, 'org.hamcrest:hamcrest-core@1.3 [%s]' % core, 'test')}
                        <= set(g.edges))

    def test_modules_do_not_share_children_of_the_same_dependency(self):
        g = dp.parse_mvn('com.example:a:jar:1.0\n'
                         '\\- org.x:lib:jar:1:compile\n'
                         '   \\- org.x:leaf:jar:1:compile\n'
                         'com.example:b:jar:1.0\n'
                         '+- org.x:lib:jar:1:compile\n'
                         '|  \\- org.x:leaf:jar:2:runtime\n'
                         '\\- org.x:native:jar:linux:3:test\n')
        a, b = 'com.example:a@1.0', 'com.example:b@1.0'
        self.assertEqual(set(g.edges), {
            (a, 'org.x:lib@1 [%s]' % a, 'compile'), ('org.x:lib@1 [%s]' % a, 'org.x:leaf@1 [%s]' % a, 'compile'),
            (b, 'org.x:lib@1 [%s]' % b, 'compile'), ('org.x:lib@1 [%s]' % b, 'org.x:leaf@2 [%s]' % b, 'runtime'),
            (b, 'org.x:native:jar:linux@3 [%s]' % b, 'test')})
        self.assertEqual(dp.render(g), '''\
com.example:a 1.0
└── org.x:lib 1 (compile)
    └── org.x:leaf 1 (compile)
com.example:b 1.0
├── org.x:lib 1 (compile)
│   └── org.x:leaf 2 (runtime)
└── org.x:native:jar:linux 3 (test)''')
        for bad in ('[INFO] Scanning for projects...\n',
                    'com.example:app:jar:1.0.0\n\\- (g:a:jar:1:compile - omitted for duplicate)\n'):
            self.assertRaises(dp.Fail, dp.parse_mvn, bad)

    def test_cli_reads_tree_from_temporary_output_file(self):
        self.fake('mvn', '[INFO] BUILD SUCCESS\n', body='out = [a for a in sys.argv if a.startswith("-DoutputFile=")][0][13:]\n'
                  'open(out, "w", encoding="utf-8").write(%r)' % fixture('mvn-tree.txt'))
        r = self.dp('mvn', '--', '-Pci')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith('com.example:parent:pom 1.0.0\ncom.example:core 1.0.0\n'), r.stdout)
        self.assertNotIn('INFO', r.stdout)
        argv = self.log('mvn')['argv']
        self.assertEqual(argv[:5], ['-o', '-B', 'dependency:tree', '-DoutputType=text', '-DappendOutput=true'])
        self.assertEqual(argv[-1], '-Pci')
        self.assertFalse(argv[5].startswith('-DoutputFile=' + self.proj))

    def test_cli_failures(self):
        self.fake('mvn', '[ERROR] Failed to execute goal\n', code=1)
        r = self.dp('mvn')
        self.assertEqual(r.returncode, 2)
        self.assertIn('[ERROR] Failed to execute goal', r.stderr)
        self.assertIn('command failed (exit 1): mvn -o -B dependency:tree', r.stderr)
        self.fake('mvn')
        r = self.dp('mvn')
        self.assertEqual(r.returncode, 2)
        self.assertIn('no dependency tree', r.stderr)


class GradleTest(Scratch):
    def test_parse_configurations_markers_and_failures(self):
        g = dp.parse_gradle(fixture('gradle-dependencies.txt'))
        self.assertEqual(g.roots, ['configuration:annotationProcessor', 'configuration:compileClasspath',
                                   'configuration:implementation', 'configuration:runtimeClasspath'])
        self.assertTrue(g.problem)
        cc = ' [compileClasspath]'
        self.assertTrue({
            ('configuration:compileClasspath', 'org.slf4j:slf4j-api@2.0.9' + cc, 'requested 1.7.30'),
            ('configuration:compileClasspath', 'org.apache.commons:commons-lang3@3.12.0' + cc, 'constraint'),
            ('project :lib' + cc, 'com.google.guava:guava@31.0-jre' + cc, None),
            ('com.google.guava:guava@31.0-jre' + cc, 'com.google.guava:failureaccess@1.0.1' + cc, None),
            ('configuration:implementation', 'com.google.guava:guava@31.0-jre [implementation]', 'not resolved'),
            ('configuration:runtimeClasspath', 'com.example:missing@1.0 [runtimeClasspath] (failed)', None),
        } <= set(g.edges))
        self.assertIn('''\
compileClasspath
├── com.google.guava:guava 31.0-jre
│   ├── com.google.guava:failureaccess 1.0.1
│   └── com.google.guava:listenablefuture 9999.0-empty-to-avoid-conflict-with-guava
├── org.slf4j:slf4j-api 2.0.9 (requested 1.7.30)
├── project :lib
│   └── com.google.guava:guava 31.0-jre (*)
└── org.apache.commons:commons-lang3 3.12.0 (constraint)
implementation''', dp.render(g))
        self.assertRaises(dp.Fail, dp.parse_gradle, 'BUILD SUCCESSFUL in 1s\n')

    def test_cli_failed_dependency_exits_1(self):
        self.fake('gradle', fixture('gradle-dependencies.txt'))
        r = self.dp('gradle', '--depth', '0')
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout, 'annotationProcessor\ncompileClasspath\nimplementation\nruntimeClasspath\n')
        self.assertEqual(self.log('gradle')['argv'], ['--offline', '--console=plain', '-q', 'dependencies'])


class DotnetTest(Scratch):
    def setUp(self):
        super().setUp()
        self.csproj = os.path.join(self.proj, 'App.csproj')
        self.assets = os.path.join(self.proj, 'obj', 'project.assets.json')
        write(self.csproj, '<Project Sdk="Microsoft.NET.Sdk" />')
        write(self.assets, fixture('dotnet/project.assets.json'))
        os.utime(self.csproj, (1000000000, 1000000000))

    def test_per_framework_graph_from_assets_without_running_dotnet(self):
        r = self.dp('dotnet', '--format', 'json')
        self.assertEqual(r.returncode, 0, r.stderr)
        snap = json.loads(r.stdout)
        self.assertEqual(snap['roots'], ['App@1.0.0 [net8.0]', 'App@1.0.0 [net48]'])
        self.assertTrue({('App@1.0.0 [net8.0]', 'Lib@1.0.0 [net8.0]', None),
                         ('Lib@1.0.0 [net8.0]', 'Newtonsoft.Json@13.0.3 [net8.0]', None),
                         ('Serilog.Sinks.Console@5.0.0 [net8.0]', 'Serilog@3.1.1 [net8.0]', None),
                         ('App@1.0.0 [net48]', 'Newtonsoft.Json@12.0.3 [net48]', None),
                         ('System.Memory@4.5.5 [net48]', 'System.Buffers@4.5.1 [net48]', None)} <= edges(snap))
        self.assertTrue(self.dp('nuget').stdout.startswith('App 1.0.0 [net8.0]\n├── Lib 1.0.0\n'))

    def test_refuses_missing_stale_flat_or_ambiguous_inputs(self):
        os.utime(self.assets, (999999000, 999999000))
        r = self.dp('dotnet')
        self.assertEqual(r.returncode, 2)
        self.assertIn('older than', r.stderr)
        os.remove(self.assets)
        r = self.dp('dotnet')
        self.assertIn('dotnet restore', r.stderr)
        write(os.path.join(self.proj, 'packages.config'), '<packages />')
        r = self.dp('dotnet')
        self.assertEqual(r.returncode, 2)
        self.assertIn('flat', r.stderr)
        write(os.path.join(self.proj, 'Other.fsproj'), '<Project />')
        r = self.dp('dotnet')
        self.assertEqual(r.returncode, 2)
        self.assertIn('App.csproj, Other.fsproj', r.stderr)

    def fake_sdk(self, version):
        self.fake('dotnet', 'Project App\n', body='if sys.argv[1:] == ["--version"]: print(%r); sys.exit(0)' % version)

    def test_native_command_follows_selected_sdk_and_tree_rejects_native_args(self):
        self.fake_sdk('10.0.100')
        r = self.dp('dotnet', '--format', 'native', '--', '--framework', 'net8.0')
        self.assertEqual((r.returncode, r.stdout), (0, 'Project App\n'), r.stderr)
        self.assertEqual(self.log('dotnet')['argv'], ['package', 'list', '--project', self.csproj,
                                                      '--include-transitive', '--no-restore', '--framework', 'net8.0'])
        for sdk in ('8.0.404', '9.0.100-rc.1.24452.12'):
            self.fake_sdk(sdk)
            r = self.dp('dotnet', '--format', 'native')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.log('dotnet')['argv'], ['list', self.csproj, 'package', '--include-transitive'])
        self.fake_sdk('not a version')
        r = self.dp('dotnet', '--format', 'native')
        self.assertEqual(r.returncode, 2)
        self.assertIn('dotnet --version', r.stderr)
        self.assertEqual(self.log('dotnet')['argv'], ['--version'])
        self.assertEqual(self.dp('dotnet', '--', '--framework', 'net8.0').returncode, 2)

    def test_malformed_assets_shapes_exit_2_without_traceback(self):
        good = json.loads(fixture('dotnet/project.assets.json'))

        def mutate(path, value):
            data = json.loads(json.dumps(good))
            *parents, last = path
            for key in parents:
                data = data[key]
            data[last] = value
            return data
        cases = [mutate(['project'], []), mutate(['project', 'restore'], []), mutate(['targets'], {}),
                 mutate(['targets', 'net8.0'], []), mutate(['targets', 'net8.0', 'Serilog/3.1.1'], 'x'),
                 mutate(['targets', 'net8.0', 'Serilog.Sinks.Console/5.0.0', 'dependencies'], ['Serilog']),
                 mutate(['projectFileDependencyGroups', 'net8.0'], 'Lib >= 1.0.0'),
                 mutate(['projectFileDependencyGroups', 'net8.0'], [5])]
        for data in cases:
            write(self.assets, data)
            r = self.dp('dotnet')
            self.assertEqual(r.returncode, 2, (data, r.stdout))
            self.assertIn('unsupported project.assets.json', r.stderr)
            self.assertNotIn('Traceback', r.stderr)

    def test_tree_warns_freshness_is_only_checked_against_the_project_file(self):
        r = self.dp('dotnet')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('dp: warning:', r.stderr)
        self.assertIn('Directory.Packages.props', r.stderr)


class ExtraAdaptersTest(Scratch):
    def test_pnpm(self):
        g = dp.parse_pnpm(json.loads(fixture('pnpm-list.json')))
        self.assertEqual(g.roots, ['web@1.0.0'])
        self.assertTrue({('web@1.0.0', 'react@18.3.1', None), ('web@1.0.0', 'lodash-es@4.17.21', 'as lodash-alias'),
                         ('web@1.0.0', 'typescript@5.4.5', 'dev'), ('loose-envify@1.4.0', 'js-tokens@4.0.0', None)} <= set(g.edges))
        self.assertRaises(dp.Fail, dp.parse_pnpm, {'name': 'not a list'})
        write(os.path.join(self.proj, 'package.json'), {'name': 'web'})
        write(os.path.join(self.proj, 'pnpm-lock.yaml'), '')
        self.fake('pnpm', fixture('pnpm-list.json'))
        r = self.dp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log('pnpm')['argv'], ['list', '--json', '--depth', 'Infinity'])

    def test_cargo(self):
        g = dp.parse_cargo(json.loads(fixture('cargo-metadata.json')))
        reg = 'registry+https://github.com/rust-lang/crates.io-index#'
        app, tool = 'app@0.1.0 [path+file:///w/app#0.1.0]', 'tool@0.1.0 [path+file:///w/tool#0.1.0]'
        serde = 'serde@1.0.200 [%sserde@1.0.200]' % reg
        self.assertEqual(g.roots, [app, tool])
        self.assertTrue({(app, 'cc@1.0.90 [%scc@1.0.90]' % reg, 'build'),
                         (app, 'winapi@0.3.9 [%swinapi@0.3.9]' % reg, 'cfg(windows)'),
                         (tool, serde, 'dev'), (tool, app, None),
                         (serde, 'serde_derive@1.0.200 [%sserde_derive@1.0.200]' % reg, None)} <= set(g.edges))
        self.assertRaises(dp.Fail, dp.parse_cargo, dict(json.loads(fixture('cargo-metadata.json')), resolve=None))
        self.fake('cargo', fixture('cargo-metadata.json'))
        self.assertEqual(self.dp('cargo').returncode, 0)
        self.assertEqual(self.log('cargo')['argv'], ['metadata', '--format-version', '1', '--frozen'])
        self.dp('cargo', '--format', 'native')
        self.assertEqual(self.log('cargo')['argv'], ['tree', '--frozen'])

    def test_cargo_same_name_version_from_two_sources_stays_two_nodes(self):
        """Fixture-only proof (no cargo installed): opaque package ids keep sources apart."""
        reg = 'registry+https://github.com/rust-lang/crates.io-index#log@0.4.21'
        git = 'git+https://github.com/rust-lang/log?branch=master#log@0.4.21'

        def meta(*log_ids):
            pkgs = [('app', '0.1.0', 'path+file:///w/app#0.1.0'), ('a', '1.0.0', 'path+file:///w/a#1.0.0'),
                    ('b', '1.0.0', 'path+file:///w/b#1.0.0')] + [('log', '0.4.21', i) for i in log_ids]
            dep = lambda pkg: {'name': pkg.split('#')[1].split('@')[0], 'pkg': pkg, 'dep_kinds': [{'kind': None, 'target': None}]}
            nodes = [{'id': 'path+file:///w/app#0.1.0', 'deps': [dep(i) for i in log_ids]},
                     {'id': 'path+file:///w/a#1.0.0', 'deps': []}, {'id': 'path+file:///w/b#1.0.0', 'deps': []}]
            nodes += [{'id': i, 'deps': [dep('path+file:///w/%s#1.0.0' % ('a' if i == reg else 'b'))]} for i in log_ids]
            return {'packages': [{'name': n, 'version': v, 'id': i} for n, v, i in pkgs],
                    'workspace_members': ['path+file:///w/app#0.1.0'], 'resolve': {'nodes': nodes, 'root': None}}

        g = dp.parse_cargo(meta(reg, git))
        rlog, glog = 'log@0.4.21 [%s]' % reg, 'log@0.4.21 [%s]' % git
        self.assertTrue({rlog, glog} <= set(g.nodes))
        children = {(a, b) for a, b, _ in g.edges if a in (rlog, glog)}
        self.assertEqual(children, {(rlog, 'a@1.0.0 [path+file:///w/a#1.0.0]'), (glog, 'b@1.0.0 [path+file:///w/b#1.0.0]')})

        for name, ids in (('before.json', [reg]), ('after.json', [git])):
            self.fake('cargo', json.dumps(meta(*ids)))
            write(os.path.join(self.tmp, name), self.dp('cargo', '--format', 'json').stdout)
        r = self.dp('--diff', os.path.join(self.tmp, 'before.json'), os.path.join(self.tmp, 'after.json'))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('+ %s' % glog, r.stdout)
        self.assertIn('- %s' % rlog, r.stdout)

    def test_go_requirement_graph_keeps_every_version(self):
        g = dp.parse_go(fixture('go-mod-graph.txt'))
        self.assertEqual(g.roots, ['example.com/app'])
        self.assertTrue({('example.com/app', 'golang.org/x/text@v0.14.0', None),
                         ('rsc.io/sampler@v1.3.0', 'golang.org/x/text@v0.0.0-20170915032832-14c0d48ead0c', None)} <= set(g.edges))
        self.assertRaises(dp.Fail, dp.parse_go, 'one-field-only\n')
        write(os.path.join(self.proj, 'go.mod'), 'module example.com/empty\n\ngo 1.21\n')
        self.fake('go', body='sys.stderr.write("GOTOOLCHAIN=%s\\n" % os.environ.get("GOTOOLCHAIN"))')
        r = self.dp(GOTOOLCHAIN='auto')
        self.assertEqual((r.returncode, r.stdout), (0, 'example.com/empty\n'), r.stderr)
        self.assertIn('GOTOOLCHAIN=local', r.stderr)
        self.assertEqual(self.log('go')['argv'], ['mod', 'graph'])

    def test_composer_child_versions_are_installed_not_constraints(self):
        g = dp.parse_composer(json.loads(fixture('composer-show-tree.json')))
        self.assertEqual(g.roots, ['project:project'])
        self.assertTrue({('project:project', 'monolog/monolog@3.5.0', None),
                         ('monolog/monolog@3.5.0', 'psr/log@3.0.0', '^2.0 || ^3.0'), ('monolog/monolog@3.5.0', 'php', '>=8.1'),
                         ('symfony/console@v6.4.1', 'symfony/polyfill-mbstring@v1.28.0', '~1.0')} <= set(g.edges))
        self.assertRaises(dp.Fail, dp.parse_composer, [])
        self.fake('composer', '{"installed": []}')
        r = self.dp('composer')
        self.assertEqual((r.returncode, r.stdout), (0, 'project\n'))
        self.assertEqual(self.log('composer')['argv'],
                         ['--no-plugins', '--no-scripts', '--no-interaction', 'show', '--tree', '--format=json'])

    def test_child_environment_guards_override_inherited_values(self):
        self.fake('bundle', body='print(" ".join(os.environ.get(k, "-") for k in '
                                 '("BUNDLE_FROZEN", "GOTOOLCHAIN", "COREPACK_ENABLE_NETWORK")))')
        r = self.dp('bundle', '--format', 'native', BUNDLE_FROZEN='false', GOTOOLCHAIN='auto',
                    COREPACK_ENABLE_NETWORK='1')
        self.assertEqual((r.returncode, r.stdout), (0, 'true local 0\n'), r.stderr)

    def test_malformed_backend_fields_are_errors_not_tracebacks(self):
        for out in ('{"name": "app", "version": "1", "dependencies": {"x": {"version": []}}}',
                    '{"name": ["app"], "version": "1"}'):
            self.fake('npm', out)
            r = self.dp('npm')
            self.assertEqual(r.returncode, 2, out)
            self.assertIn('unsupported npm output', r.stderr)
            self.assertNotIn('Traceback', r.stderr)

    def test_native_only_tools(self):
        self.fake('yarn')
        r = self.dp('yarn')
        self.assertEqual(r.returncode, 2)
        self.assertIn('--format native', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.bin, 'yarn.log')))
        for tool, argv in (('yarn', ['list']), ('bun', ['pm', 'ls', '--all']), ('bundler', ['list']),
                           ('swift', ['package', '--force-resolved-versions', '--skip-update',
                                      'show-dependencies', '--format', 'text']),
                           ('poetry', ['show', '--tree'])):
            exe = 'bundle' if tool == 'bundler' else tool
            self.fake(exe, 'native\n')
            r = self.dp(tool, '--format', 'native')
            self.assertEqual((r.returncode, r.stdout, self.log(exe)['argv']), (0, 'native\n', argv), tool)
        write(os.path.join(self.proj, '.yarnrc.yml'), '')
        self.dp('yarn', '--format', 'native')
        self.assertEqual(self.log('yarn')['argv'], ['info', '--all', '--recursive'])


class DetectTest(Scratch):
    def test_mixed_projects_are_refused_with_instructions(self):
        for f in ('pom.xml', 'package.json', 'package-lock.json'):
            write(os.path.join(self.proj, f), '{}')
        r = self.dp()
        self.assertEqual(r.returncode, 2)
        self.assertIn("dp mvn -C '%s'" % self.proj, r.stderr)
        self.assertIn("dp npm -C '%s'" % self.proj, r.stderr)
        self.fake('npm', '{"name": "x", "version": "1"}')
        self.assertEqual(self.dp('npm').stdout, 'x 1\n')

    def test_javascript_needs_lockfile_or_package_manager_evidence(self):
        write(os.path.join(self.proj, 'package.json'), {'name': 'x'})
        r = self.dp()
        self.assertEqual(r.returncode, 2)
        self.assertIn('dp pnpm -C', r.stderr)
        write(os.path.join(self.proj, 'package.json'), {'packageManager': 'yarn@4.1.0'})
        self.fake('yarn', 'yarn tree\n')
        r = self.dp('--format', 'native')
        self.assertEqual((r.returncode, r.stdout), (0, 'yarn tree\n'))
        write(os.path.join(self.proj, 'package-lock.json'), '{}')
        self.assertEqual(self.dp().returncode, 2)

    def test_nothing_detected(self):
        r = self.dp()
        self.assertEqual(r.returncode, 2)
        self.assertIn('no supported project', r.stderr)


@unittest.skipUnless(shutil.which('uv'), 'uv not on PATH')
class RealUvTest(Scratch):
    """Live uv: scratch venv/lock with no dependencies, offline, private uv cache."""

    def setUp(self):
        super().setUp()
        self.env = {'UV_CACHE_DIR': os.path.join(self.tmp, 'uv-cache'), 'UV_PYTHON': sys.executable}
        self.path = self.bin + os.pathsep + os.path.dirname(shutil.which('uv'))

    def uv(self, *args):
        subprocess.run(['uv'] + list(args), cwd=self.proj, env=dict(os.environ, **self.env), check=True,
                       capture_output=True)

    def test_empty_environment(self):
        venv = os.path.join(self.tmp, 'venv é')
        self.uv('venv', '--offline', '--no-python-downloads', venv)
        self.fake('pip', 'sys.executable: %s\n' % os.path.join(venv, 'bin', 'python'))
        r = self.dp('pip', '--format', 'json', path=self.path, **self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([n['id'] for n in json.loads(r.stdout)['nodes']], ['environment:environment'])

    def test_locked_project_without_dependencies(self):
        write(os.path.join(self.proj, 'pyproject.toml'),
              '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.8"\ndependencies = []\n')
        self.uv('lock', '--offline', '--no-python-downloads')
        r = self.dp('uv', path=self.path, **self.env)
        self.assertEqual((r.returncode, r.stdout), (0, 'demo 0.1.0\n'), r.stderr)


@unittest.skipUnless(shutil.which('npm'), 'npm not on PATH')
class RealNpmTest(Scratch):
    """Live npm: installs ONLY local file: packages in a scratch dir, offline, no scripts."""

    def test_local_file_dependencies_and_missing_dependency(self):
        write(os.path.join(self.proj, 'package.json'),
              {'name': 'root-app', 'version': '1.0.0', 'dependencies': {'a': 'file:./a', 'b': 'file:./b'}})
        write(os.path.join(self.proj, 'a', 'package.json'),
              {'name': 'a', 'version': '1.0.0', 'dependencies': {'b': '^2.0.0'}})
        write(os.path.join(self.proj, 'b', 'package.json'), {'name': 'b', 'version': '2.0.0'})
        env = {'npm_config_cache': os.path.join(self.tmp, 'npm-cache'), 'npm_config_update_notifier': 'false'}
        subprocess.run(['npm', 'install', '--ignore-scripts', '--no-audit', '--no-fund', '--offline'],
                       cwd=self.proj, env=dict(os.environ, **env), check=True, capture_output=True)
        r = self.dp('npm', '--format', 'json', path=os.environ['PATH'], **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue({('root-app@1.0.0', 'a@1.0.0', None), ('root-app@1.0.0', 'b@2.0.0', None),
                         ('a@1.0.0', 'b@2.0.0', None)} <= edges(json.loads(r.stdout)), r.stdout)

        write(os.path.join(self.proj, 'package.json'), {'name': 'root-app', 'version': '1.0.0', 'dependencies': {
            'a': 'file:./a', 'b': 'file:./b', 'left-pad': '^1.3.0'}})
        r = self.dp('npm', path=os.environ['PATH'], **env)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('left-pad (required ^1.3.0) [missing]', r.stdout)


if __name__ == '__main__':
    unittest.main()
