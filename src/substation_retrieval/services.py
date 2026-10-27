"""Wires settings, stores, engines and the ingest service together (one instance per process)."""
from __future__ import annotations

import logging
import threading

from .config import Settings, settings as default_settings
from .extraction.llm import LLMClient
from .ingest.jobs import JobDB
from .ingest.service import IngestService
from .kg.matcher import KGEngine
from .kg.store import open_store
from .rag.engine import RAGEngine
from .rag.index import IndexDB
from .retrieval.orchestrator import Orchestrator

log = logging.getLogger(__name__)


class Services:
    def __init__(self, s: Settings | None = None):
        self.settings = s or default_settings()
        self.store = open_store(self.settings, log.info)
        self.index = IndexDB(self.settings.var("db", "index.sqlite"), self.settings.var_dir)
        self.jobs = JobDB(self.settings.var("db", "jobs.sqlite"))
        self.llm = LLMClient(self.settings, self.settings.var_dir / "cache" / "llm")
        self.ingest = IngestService(self.settings, self.store, self.index, self.jobs)
        embedder = None
        if self.settings.get("engines.rag.embedding", "none") == "openai" and self.llm.available:
            embedder = self.llm.embed
        self.kg = KGEngine(self.store, self.index, self.settings)
        self.rag = RAGEngine(self.index, self.settings, embedder)
        self.orchestrator = Orchestrator(self.kg, self.rag, self.settings)
        self.ingest.ensure_ontology()
        self._watcher: threading.Thread | None = None

    def stop_workers(self):
        if self.ingest.pool:
            self.ingest.pool.stop()
            self.ingest.pool = None

    def close(self):
        self.stop_workers()
        self.store.close()


_SERVICES: Services | None = None


def get_services() -> Services:
    global _SERVICES
    if _SERVICES is None:
        _SERVICES = Services()
    return _SERVICES
