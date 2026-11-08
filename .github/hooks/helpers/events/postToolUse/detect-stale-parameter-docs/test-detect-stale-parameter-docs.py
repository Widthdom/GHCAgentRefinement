import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hook_path = os.path.join(_script_directory, 'detect-stale-parameter-docs.py')
_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_script_directory))))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests

_get_hook_entry = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-entry.py'))['get_hook_entry']


_CONTEXT_PREFIX = 'The parameter documentation no longer matches the signature'

_GOOGLE_SOURCE = '''class Parser:
    def parse(self, text, *, strict=False):
        """Parse the text.

        Args:
            source (str): The text.
                This line continues the description.
            strict: Whether to reject unknown tokens.

        Returns:
            int: The count.
        """
        return len(text)


def consistent(text):
    """Count the text.

    Args:
        text: The text.
    """
    return len(text)


def untouched(value):
    """Args:
        other: Stale but not edited.
    """
'''

_CSHARP_SOURCE = '''public static class Calculator
{
    /// <summary>Sums the values.</summary>
    /// <param name="values">The values.</param>
    /// <param name="offset">The offset.</param>
    [Pure]
    public static int Sum(this IEnumerable<int> values, Dictionary<string, int> weights, string separator = "a,b",
        params int[] rest) => 0;

    /// <param name="x">X.</param>
    public static int Twice(int x) => x * 2;
}
'''


class DetectStaleParameterDocsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-stale-parameter-docs-test-')
        self.addCleanup(temporary.cleanup)
        self.__root = temporary.name

    def __write(self, relative_path, content):
        path = os.path.join(self.__root, *relative_path.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            file.write(content)
        return path

    def __invoke(self, tool_name='edit', tool_args=None, raw=None):
        if raw is None:
            raw = json.dumps({'cwd': self.__root, 'toolName': tool_name, 'toolArgs': tool_args}, ensure_ascii=False).encode('utf-8')
        return subprocess.run(
            [sys.executable, _hook_path], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )

    def __assert_silent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))

    def __assert_context(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertEqual(result.stderr, b'')
        self.assertFalse(result.stdout.startswith(b'\xef\xbb\xbf'))
        context = json.loads(result.stdout.decode('utf-8'))['additionalContext']
        self.assertTrue(context.startswith(_CONTEXT_PREFIX))
        for fragment in fragments:
            self.assertIn(fragment, context)
        return context

    def test_configuration(self):
        entry = _get_hook_entry(_hook_path)
        self.assertEqual(entry['matcher'], '(?:functions\\.)?(?:edit|create|insert|str_replace_editor|apply_patch)')

    def test_reports_python_signature_change(self):
        path = self.__write('src/parser.py', _GOOGLE_SOURCE)
        tool_calls = (
            ('edit', {'path': path, 'old_str': 'def parse(self, source, *, strict=False):', 'new_str': 'def parse(self, text, *, strict=False):'}),
            ('edit', {'path': 'src/parser.py', 'old_string': 'x', 'new_string': '    def parse(self, text, *, strict=False):'}),
            ('functions.str_replace_editor', {'command': 'insert', 'path': path, 'new_str': '            source (str): The text.'}),
            ('apply_patch', '*** Begin Patch\n*** Update File: src/parser.py\n@@\n-    def parse(self, source):\n+    def parse(self, text, *, strict=False):\n*** End Patch'),
        )
        for tool_name, tool_args in tool_calls:
            with self.subTest(tool_name=tool_name, tool_args=tool_args):
                context = self.__assert_context(
                    self.__invoke(tool_name, tool_args),
                    'a function you just edited',
                    '- src/parser.py:2 `parse`: documented but not in the signature: `source`; in the signature but not documented: `text`',
                )
                self.assertNotIn('untouched', context)

    def test_reports_whole_file_on_create(self):
        path = self.__write('src/parser.py', _GOOGLE_SOURCE)
        context = self.__assert_context(self.__invoke('create', {'path': path, 'file_text': _GOOGLE_SOURCE}), '2 functions', '`untouched`')
        self.assertIn('documented but not in the signature: `other`; in the signature but not documented: `value`', context)

    def test_reads_python_docstring_styles(self):
        cases = (
            ('def f(a, b):\n    """:param a: A.\n    :param int c: C.\n    """\n', '`c`', '`b`'),
            ('def f(x, y, z, **kw):\n    """Do.\n\n    Parameters\n    ----------\n    x, y : int\n        Values.\n\n    Returns\n    -------\n    w : int\n    """\n', None, '`z`'),
            ('def f(*args, value):\n    """引数:\n        *args: 値。\n        old: 古い。\n    """\n', '`old`', '`value`'),
            ('async def f(a):\n    """Args:\n        b: B.\n    """\n', '`b`', '`a`'),
        )
        for source, stale, missing in cases:
            with self.subTest(source=source):
                path = self.__write('src/module.py', source)
                context = self.__assert_context(self.__invoke('create', {'path': path, 'file_text': source}))
                if stale:
                    self.assertIn(f'documented but not in the signature: {stale}', context)
                else:
                    self.assertNotIn('documented but not in the signature', context)
                self.assertIn(f'in the signature but not documented: {missing}', context)

    def test_allows_matching_or_undocumented_python_parameters(self):
        sources = (
            'def f(a, b):\n    """Args:\n        a: A.\n        b (int): B.\n    """\n',
            'def f(a):\n    """Just a summary."""\n',
            'def f(a):\n    return a\n',
            'class C:\n    @classmethod\n    def f(cls, a):\n        """:param a: A."""\n',
            'def f(x, y, **kw):\n    """Parameters\n    ----------\n    x, y : int\n    **kw : dict\n    """\n',
            'def f(a, **kwargs):\n    """Args:\n        a: A.\n        msg: Passed through kwargs.\n    """\n',
            'def f(a, *args):\n    """:param a: A."""\n',
            'def f(a):\n    """Args:\n        a -- the value.\n    """\n',
            'def f(a:\n    pass\n',
        )
        for source in sources:
            with self.subTest(source=source):
                path = self.__write('src/module.py', source)
                self.__assert_silent(self.__invoke('create', {'path': path, 'file_text': source}))

    def test_reports_csharp_signature_change(self):
        path = self.__write('src/Calculator.cs', _CSHARP_SOURCE)
        new_signature = (
            '    public static int Sum(this IEnumerable<int> values, Dictionary<string, int> weights, string separator = "a,b",'
        )
        context = self.__assert_context(
            self.__invoke('edit', {'path': path, 'old_str': 'x', 'new_str': new_signature}),
            '- src/Calculator.cs:7 `Sum`: documented but not in the signature: `offset`; '
            'in the signature but not documented: `weights`, `separator`, `rest`',
        )
        self.assertNotIn('Twice', context)

    def test_reads_csharp_declarations(self):
        cases = (
            ('/// <param name="value">V.</param>\npublic record Point(int X, int Y);\n', '`Point`', '`value`'),
            ('/// <param name="a">A.</param>\npublic Foo(ref int a, out string @b) { }\n', '`Foo`', None),
            ('/// <param name="a">A.</param>\n\n[Obsolete]\n[Pure]\nvoid M([NotNull] T a, (int, int) pair = default) { }\n', '`M`', None),
        )
        for source, name, stale in cases:
            with self.subTest(source=source):
                path = self.__write('src/A.cs', source)
                context = self.__assert_context(self.__invoke('create', {'path': path, 'file_text': source}), name)
                if stale:
                    self.assertIn(f'documented but not in the signature: {stale}', context)

    def test_allows_matching_or_unparsable_csharp_declarations(self):
        sources = (
            '/// <param name="x">X.</param>\npublic static int Twice(int x) => x * 2;\n',
            '/// <summary>Value.</summary>\npublic int Value { get; }\n',
            '/// <summary>Only summary.</summary>\npublic void M(int a) { }\n',
            '/// <param name="x">X.</param>\npublic int Value { get; }\n',
            '/// <param name="x">X.</param>\n',
        )
        for source in sources:
            with self.subTest(source=source):
                path = self.__write('src/A.cs', source)
                self.__assert_silent(self.__invoke('create', {'path': path, 'file_text': source}))

    def test_ignores_other_files_tools_and_missing_files(self):
        path = self.__write('src/parser.js', _GOOGLE_SOURCE)
        self.__assert_silent(self.__invoke('create', {'path': path, 'file_text': _GOOGLE_SOURCE}))
        python_path = self.__write('src/parser.py', _GOOGLE_SOURCE)
        self.__assert_silent(self.__invoke('bash', {'command': f'cat {python_path}'}))
        self.__assert_silent(self.__invoke('str_replace_editor', {'command': 'view', 'path': python_path}))
        self.__assert_silent(self.__invoke('create', {'path': 'src/missing.py', 'file_text': _GOOGLE_SOURCE}))
        self.__assert_silent(self.__invoke('apply_patch', '*** Begin Patch\n*** Delete File: src/parser.py\n*** End Patch'))

    def test_shared_lines_do_not_mark_functions_as_edited(self):
        path = self.__write('src/parser.py', _GOOGLE_SOURCE)
        self.__assert_silent(self.__invoke('edit', {'path': path, 'old_str': 'x', 'new_str': '        Args:\n        """'}))

    def test_lists_reports_with_limit(self):
        source = ''.join(f'def f{index:02}(a):\n    """:param b: B."""\n\n' for index in range(12))
        path = self.__write('src/many.py', source)
        context = self.__assert_context(self.__invoke('create', {'path': path, 'file_text': source}), '12 functions', '`f09`', '... and 2 more')
        self.assertNotIn('`f10`', context)

    def test_invalid_input_reports_error(self):
        valid = {'cwd': self.__root, 'toolName': 'edit', 'toolArgs': {}}
        invalid = [b'', b' \n', b'{', b'null', b'[]', b'42', b'\xff']
        for key, value in (('cwd', None), ('cwd', 'relative'), ('toolName', None), ('toolName', ' ')):
            invalid.append(json.dumps({**valid, key: value}).encode('utf-8'))
        invalid.append(json.dumps({'cwd': self.__root, 'toolName': 'edit'}).encode('utf-8'))
        for raw in invalid:
            with self.subTest(raw=raw):
                result = self.__invoke(raw=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Failed to detect stale parameter docs:', result.stderr)


if __name__ == '__main__':
    run_tests()
