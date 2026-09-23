import sqlite3

import pytest

from app.storage import SQLiteDatabase


def test_transaction_commits_and_closes(tmp_path):
    database = SQLiteDatabase(tmp_path / "state.db")
    with database.transaction(immediate=True) as connection:
        connection.execute("CREATE TABLE items (value TEXT)")
        connection.execute("INSERT INTO items VALUES ('ok')")
    with sqlite3.connect(tmp_path / "state.db") as connection:
        assert connection.execute("SELECT value FROM items").fetchone() == ("ok",)


def test_transaction_rolls_back_on_exception(tmp_path):
    database = SQLiteDatabase(tmp_path / "state.db")
    with pytest.raises(RuntimeError), database.transaction() as connection:
        connection.execute("CREATE TABLE items (value TEXT)")
        connection.execute("INSERT INTO items VALUES ('no')")
        raise RuntimeError("injected")
    with sqlite3.connect(tmp_path / "state.db") as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='items'").fetchone() is None
