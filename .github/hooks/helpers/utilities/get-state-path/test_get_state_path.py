import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

_script_directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_directory)
import get_state_path as _utility

_hooks_directory = os.path.dirname(os.path.dirname(os.path.dirname(_script_directory)))
sys.path.insert(0, os.path.join(_hooks_directory, 'tests', 'helpers'))
from hook_test_output import run_tests as _run_tests


class GetStatePathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-get-state-path-test-')
        self.addCleanup(temporary.cleanup)
        tempdir = patch.object(tempfile, 'tempdir', temporary.name)
        tempdir.start()
        self.addCleanup(tempdir.stop)
        self.__states_directory = os.path.join(temporary.name, 'ghc-agent-refinement', 'hook-states')

    def test_normalizes_session_id(self):
        # 前後の空白と大文字小文字が違っても、同じセッションは同じ保存先になる。
        session_id = uuid.uuid4()
        self.assertEqual(_utility.parse_session_id(f' {str(session_id).upper()} '), session_id)
        self.assertEqual(
            _utility.get_state_path('known-files', f' {str(session_id).upper()} '),
            os.path.join(self.__states_directory, 'known-files', f'{session_id}.sqlite3'),
        )

    def test_extension(self):
        session_id = str(uuid.uuid4())
        self.assertEqual(
            _utility.get_state_path('debug-mode', session_id, '.json'),
            os.path.join(self.__states_directory, 'debug-mode', f'{session_id}.json'),
        )

    def test_rejects_invalid_session_id(self):
        for session_id, message in (
            (None, 'sessionId must be a non-empty UUID string.'),
            ('', 'sessionId must be a non-empty UUID string.'),
            (' ', 'sessionId must be a non-empty UUID string.'),
            (uuid.uuid4(), 'sessionId must be a non-empty UUID string.'),
            ('not-a-uuid', 'sessionId must be a valid UUID.'),
            # パスの区切りを含む値で、状態の保存先の外を指させない。
            ('../escape', 'sessionId must be a valid UUID.'),
        ):
            with self.subTest(session_id=session_id):
                for call in (
                    lambda: _utility.parse_session_id(session_id),
                    lambda: _utility.get_state_path('known-files', session_id),
                ):
                    with self.assertRaises(ValueError) as context:
                        call()
                    self.assertEqual(str(context.exception), message)


class StateDatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-state-database-test-')
        self.addCleanup(temporary.cleanup)
        # 保存先のフォルダーはまだない。
        self.__state_path = os.path.join(temporary.name, 'states', 'state.sqlite3')

    def __open(self):
        return _utility.open_state_database(
            self.__state_path,
            'CREATE TABLE IF NOT EXISTS items (key TEXT PRIMARY KEY)',
            'CREATE TABLE IF NOT EXISTS others (value TEXT)',
        )

    def __read(self, query='SELECT key FROM items ORDER BY key'):
        return _utility.read_state_database(self.__state_path, query)

    def test_creates_folder_and_tables_and_commits(self):
        with self.__open() as connection:
            self.assertTrue(connection.in_transaction)
            connection.execute('INSERT INTO items (key) VALUES (?)', ('a',))
        self.assertEqual(self.__read(), [('a',)])
        self.assertEqual(
            self.__read("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"), [('items',), ('others',)],
        )

    def test_rolls_back_on_exception(self):
        with self.__open() as connection:
            connection.execute('INSERT INTO items (key) VALUES (?)', ('a',))
        with self.assertRaises(RuntimeError), self.__open() as connection:
            connection.execute('INSERT INTO items (key) VALUES (?)', ('b',))
            raise RuntimeError
        with self.assertRaises(sqlite3.IntegrityError), self.__open() as connection:
            connection.execute('INSERT INTO items (key) VALUES (?)', ('c',))
            connection.execute('INSERT INTO items (key) VALUES (?)', ('a',))
        self.assertEqual(self.__read(), [('a',)])

    def test_read_without_state_or_table(self):
        # 状態ファイルや表がまだなければ、記録がないとみなす。状態ファイルは作らない。
        self.assertEqual(self.__read(), [])
        self.assertFalse(os.path.exists(self.__state_path))
        os.makedirs(os.path.dirname(self.__state_path))
        sqlite3.connect(self.__state_path).close()
        self.assertEqual(self.__read(), [])

    def test_invalid_state_file(self):
        os.makedirs(os.path.dirname(self.__state_path))
        with open(self.__state_path, 'wb') as file:
            file.write(b'not a database')
        with self.assertRaises(sqlite3.Error), self.__open():
            pass


if __name__ == '__main__':
    _run_tests()
