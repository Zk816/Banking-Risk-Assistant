"""Short-term and long-term memory.

Short-term: this session only, plain Python. The last N turns plus a "context"
of slots (last company, last metric) that lets a follow-up question like
"а у Citigroup?" borrow what it did not say.

Long-term: Postgres, survives restarts. A profile per user (which companies they
follow) and every question asked. Written only if the user consented, and keyed
by a salted hash, never the raw user id.
"""

import hashlib
import json
import re
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

from kzbank.agent.perception import Perception
from kzbank.config import settings
from kzbank.storage.db import connect, fetch_all, fetch_one

SHORT_TERM_TURNS = 10
_ALL_BANKS = re.compile(r"как(ие|их|ой|ом)\s+банк|все\s+банк|всех\s+банк|which banks|all banks", re.IGNORECASE)
FOCUS_TOP_N = 3


@dataclass
class Turn:
    query: str
    intent: str
    companies: list[str]
    metrics: list[str]
    answer: str


@dataclass
class ShortTermMemory:
    """What happened in this session. Gone when the program exits."""

    turns: deque[Turn] = field(default_factory=lambda: deque(maxlen=SHORT_TERM_TURNS))
    context: dict[str, Any] = field(default_factory=dict)   # last_companies, last_metrics, ...
    selected: list[str] = field(default_factory=list)       # what attention picked last time

    def remember(self, p: Perception, answer: str, selected: list[str]) -> None:
        self.turns.append(Turn(p.text, p.intent, p.companies, p.metrics, answer))
        if p.companies:
            self.context["last_companies"] = p.companies
        if p.metrics:
            self.context["last_metrics"] = p.metrics
        if p.ratios:
            self.context["last_ratios"] = p.ratios
        self.context["last_intent"] = p.intent
        self.selected = selected

    def fill_gaps(self, p: Perception) -> list[str]:
        """Complete a follow-up question from context. Returns what was filled.

        "А у Citigroup?" has a company but no metric; "а прибыль?" has a metric
        but no company. The missing half comes from the previous turn.
        """
        filled: list[str] = []
        if not (p.follow_up or (p.companies and not p.metrics and not p.ratios) or
                (p.metrics and not p.companies)):
            return filled
        if not p.metrics and not p.ratios and self.context.get("last_metrics"):
            p.metrics = list(self.context["last_metrics"])
            filled.append(f"metric <- short-term memory: {p.metrics}")
        if not p.ratios and not p.metrics and self.context.get("last_ratios"):
            p.ratios = list(self.context["last_ratios"])
            filled.append(f"ratio <- short-term memory: {p.ratios}")
        # "У каких банков ..." asks about all banks: borrowing last turn's two
        # banks would silently shrink a market-wide ranking to them.
        if not p.companies and self.context.get("last_companies") and not _ALL_BANKS.search(p.text):
            p.companies = list(self.context["last_companies"])
            filled.append(f"company <- short-term memory: {p.companies}")
        if filled and p.intent == "legal":
            p.intent = "compliance" if p.ratios else "metric"
            filled.append(f"intent re-read as {p.intent}")
        return filled


def user_ref(user_id: str) -> str:
    """Pseudonymous id. The raw id (e-mail, phone, name) is never written."""
    return hashlib.sha256(f"{settings.memory_salt}:{user_id}".encode()).hexdigest()[:16]


class LongTermMemory:
    """Profile + query history in Postgres, keyed by a hashed user id."""

    def __init__(self, user_id: str, *, consent: bool) -> None:
        self.ref = user_ref(user_id)
        self.consent = consent
        self.session_id = uuid.uuid4().hex[:8]
        with connect() as conn:
            conn.execute(
                "INSERT INTO user_profile (user_ref, consent) VALUES (%s, %s) "
                "ON CONFLICT (user_ref) DO UPDATE SET consent = EXCLUDED.consent, updated_at = now()",
                (self.ref, consent),
            )

    # ---- write ------------------------------------------------------------
    def write(self, p: Perception, answer: str) -> bool:
        """Store the turn and update the profile. No consent, no write."""
        if not self.consent:
            return False
        with connect() as conn:
            conn.execute(
                "INSERT INTO query_history (user_ref, session_id, query, intent, companies, answer) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (self.ref, self.session_id, p.text, p.intent, p.companies, answer),
            )
            if p.companies:
                row = conn.execute("SELECT focus_companies FROM user_profile WHERE user_ref = %s",
                                   (self.ref,)).fetchone()
                counts = Counter(row["focus_companies"] if row else {})
                counts.update(p.companies)
                conn.execute("UPDATE user_profile SET focus_companies = %s, updated_at = now() "
                             "WHERE user_ref = %s", (json.dumps(counts), self.ref))
        return True

    # ---- read -------------------------------------------------------------
    def focus_companies(self, n: int = FOCUS_TOP_N) -> list[str]:
        """The companies this user asks about most, across all past sessions."""
        row = fetch_one("SELECT focus_companies FROM user_profile WHERE user_ref = %s", (self.ref,))
        counts = Counter(row["focus_companies"] if row else {})
        return [c for c, _ in counts.most_common(n)]

    def history(self, limit: int = 5) -> list[dict[str, Any]]:
        return fetch_all(
            "SELECT query, intent, companies, left(answer, 120) AS answer, created_at "
            "FROM query_history WHERE user_ref = %s ORDER BY created_at DESC LIMIT %s",
            (self.ref, limit),
        )

    def asked_before(self, text: str) -> dict[str, Any] | None:
        """The same question from an earlier session, if there was one."""
        return fetch_one(
            "SELECT answer, created_at FROM query_history "
            "WHERE user_ref = %s AND lower(query) = lower(%s) AND session_id <> %s "
            "ORDER BY created_at DESC LIMIT 1",
            (self.ref, text, self.session_id),
        )

    def forget(self) -> None:
        """Right to erasure: delete everything stored about this user."""
        with connect() as conn:
            conn.execute("DELETE FROM user_profile WHERE user_ref = %s", (self.ref,))
