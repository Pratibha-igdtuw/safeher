"""Shared pytest fixtures for the SafeHer test suite.

Each test gets its own throwaway SQLite file (created fresh via init_db())
so tests never see each other's data and can run in any order / in
parallel. app.config["DATABASE_PATH"] is monkeypatched per-test since
app.get_db() reads the path from there on every call.
"""
import os
import sys
import tempfile
import json

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client backed by a fresh, isolated SQLite DB file."""
    db_file = tmp_path / "test_safeher.db"
    app_module.app.config["DATABASE_PATH"] = str(db_file)
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    app_module.DB_PATH = str(db_file)

    app_module.init_db()

    with app_module.app.test_client() as test_client:
        yield test_client


def signup(client, email="alice@example.com", password="password123"):
    resp = client.post(
        "/signup",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
    )
    return resp


def login(client, email="alice@example.com", password="password123"):
    resp = client.post(
        "/login",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
    )
    return resp


def logout(client):
    return client.get("/logout")