"""
Run the REAL Mailer Agent as a separate process for cross-service tests.

LeadBoost and the Mailer are separate repositories and separate deployments, and their
pinned dependencies are not mutually installable (e.g. `cryptography`), so the Mailer is
never imported into the LeadBoost test process. It is started with ITS OWN interpreter
and LeadBoost's real client talks to it over real HTTP -- the same shape as production.

Configuration (environment):
  MAILER_REPO_PATH   path to a checkout of the Mailer Agent repository   (required, else skip)
  MAILER_PYTHON      interpreter that has the Mailer's requirements       (default: python3)

The Mailer runs against a throwaway SQLite file, in dry-run mode (LIVE_SENDING_ENABLED unset),
with its in-process scheduler OFF and no dispatch worker: nothing can send mail, call an LLM
or poll IMAP. The test seeds only what the (not running) async worker would have produced.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

KEYS = {"e2e-key-a": "tenant-a", "e2e-key-b": "tenant-b"}  # Mailer API key -> Mailer tenant


class MailerUnavailableForTests(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MailerProcess:
    def __init__(self, repo: str, python: str):
        self.repo, self.python = repo, python
        self.tmp = tempfile.TemporaryDirectory(prefix="mailer-e2e-")
        self.db_path = os.path.join(self.tmp.name, "mailer.db")
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._proc: Optional[subprocess.Popen] = None
        self._log = os.path.join(self.tmp.name, "server.log")

    # ------------------------------------------------------------------ lifecycle
    def _env(self) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "PYTHONPATH")}
        env.update(
            DATABASE_URL=f"sqlite:///{self.db_path}",
            ORG_KEY_MAP=json.dumps(KEYS),
            RUN_SCHEDULER_IN_PROCESS="false",
            LIVE_SENDING_ENABLED="false",
            AUTO_REPLY_ENABLED="false",
            LEADBOOST_INTEGRATION_SENDER_EMAIL="outreach@mailer.example.com",
            GROQ_API_KEY="",
            MAILBOX_ENCRYPTION_KEY=subprocess.run(
                [
                    self.python,
                    "-c",
                    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
        )
        return env

    def start(self) -> "MailerProcess":
        try:
            subprocess.run(
                [self.python, "-c", "import mailer_agent"],
                cwd=self.repo,
                capture_output=True,
                check=True,
                timeout=60,
            )
        except Exception as exc:  # no interpreter / missing deps / wrong path
            raise MailerUnavailableForTests(
                f"cannot import mailer_agent with {self.python!r} in {self.repo!r}"
            ) from exc
        env = self._env()
        subprocess.run(
            [
                self.python,
                "-c",
                "from mailer_agent.models import Base; from mailer_agent.db import engine; Base.metadata.create_all(engine)",
            ],
            cwd=self.repo,
            env=env,
            check=True,
            capture_output=True,
            timeout=120,
        )
        with open(self._log, "wb") as log:
            self._proc = subprocess.Popen(
                [
                    self.python,
                    "-m",
                    "uvicorn",
                    "mailer_agent.api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                    "--log-level",
                    "warning",
                ],
                cwd=self.repo,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        deadline = time.time() + 60
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise MailerUnavailableForTests(f"Mailer exited early:\n{self.log_tail()}")
            try:  # alive == it answers (401: the route exists and requires a key)
                urllib.request.urlopen(
                    f"{self.base_url}/integrations/leadboost/outreach-actions/x/conversation", timeout=2
                )
            except urllib.error.HTTPError:
                return self
            except Exception:
                time.sleep(0.3)
        raise MailerUnavailableForTests(f"Mailer did not become ready:\n{self.log_tail()}")

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self.tmp.cleanup()

    def log_tail(self, n: int = 2000) -> str:
        try:
            return open(self._log, errors="replace").read()[-n:]
        except OSError:
            return ""

    # ------------------------------------------------------------------ Mailer database (test seeding / inspection)
    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def rows(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        conn = self._db()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def execute(self, sql: str, params: tuple = ()) -> int:
        conn = self._db()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def dump(self) -> Dict[str, list]:
        conn = self._db()
        try:
            tables = [
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            ]
            return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}
        finally:
            conn.close()

    def dispatch(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        found = self.rows("SELECT * FROM external_dispatches WHERE idempotency_key = ?", (idempotency_key,))
        return found[0] if found else None

    def mailbox(self, tenant: str) -> Dict[str, Any]:
        return self.rows("SELECT * FROM mailboxes WHERE organization_id = ?", (tenant,))[0]

    # What the Mailer's asynchronous worker does after acceptance -- simulated, never run here.
    def worker_generated_and_resolved(
        self,
        idempotency_key: str,
        *,
        subject: str,
        body: str,
        state: str,
        at: str = "2026-01-01 12:00:00.000000",
    ) -> int:
        d = self.dispatch(idempotency_key)
        mid = self.execute(
            "INSERT INTO messages (contact_id, direction, message_type, subject, body, status, created_at) "
            "VALUES (?, 'outbound', 'initial_outreach', ?, ?, ?, ?)",
            (d["contact_id"], subject, body, state, at),
        )
        self.execute(
            "UPDATE external_dispatches SET message_id = ?, state = ? WHERE id = ?", (mid, state, d["id"])
        )
        return mid

    def add_inbound(
        self,
        idempotency_key: str,
        *,
        body: str,
        mailbox_id: Optional[int],
        subject: str = "Re: Quick question",
        at: str = "2026-01-01 13:00:00.000000",
    ) -> int:
        d = self.dispatch(idempotency_key)
        return self.execute(
            "INSERT INTO messages (contact_id, direction, subject, body, status, mailbox_id, created_at) "
            "VALUES (?, 'inbound', ?, ?, 'received', ?, ?)",
            (d["contact_id"], subject, body, mailbox_id, at),
        )
