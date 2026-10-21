"""Runs before any test module is imported: every test run gets its own throwaway database file."""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='incubrix-test-')}/test.db"
os.environ["CALLBACK_BACKOFF_S"] = "0.01"
