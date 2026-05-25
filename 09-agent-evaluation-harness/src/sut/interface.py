"""System-under-test interface.

Every RAG pipeline the harness evaluates must implement this protocol:
  - `name`: short id used in result filenames and the report
  - `run(question, contract_doc_id) -> RunOutput`: one query against the SUT,
     restricted to a single document (so we can score retrieval against the
     CUAD label spans on that doc).

We run each SUT in a subprocess via the project-01 CLI. That keeps the
harness completely decoupled from the SUT's Python environment, dependency
versions, or LLM providers — exactly the property a real regression suite
needs when it tests multiple stacks.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class RunOutput:
    """Normalized output of one SUT call."""
    answer: str
    retrieved_chunk_ids: list[str]
    retrieved_doc_ids: list[str]
    retrieved_text: list[str]
    raw_meta: dict


class Sut(Protocol):
    name: str

    def run(self, question: str, contract_doc_id: str) -> RunOutput: ...


# ---------------------------------------------------------------------------
# Project 01 SUT — invokes its eval runner directly via Python import.
# We add project 01's root to sys.path so we can import its modules without
# a subprocess hop (faster, and lets us share the in-memory FAISS index
# across questions instead of rebuilding it each time).
# ---------------------------------------------------------------------------


class _Project01Sut:
    """Wraps a (backend, technique) combination from project 01."""

    def __init__(self, backend: str, technique: str):
        self.backend = backend
        self.technique = technique
        self.name = f"project01.{backend}.{technique}"
        self._mod = None
        self._contracts = None

    def _ensure_loaded(self) -> None:
        if self._mod is not None:
            return
        s = get_settings()
        # Load project 01's modules in an isolated namespace so they don't
        # collide with the harness's own `src.*` modules. We use
        # importlib.util.spec_from_file_location with a fully-qualified
        # synthetic package name (`p01_*`) under which all of project 01's
        # `src` lives.
        import importlib.util
        import sys as _sys
        from types import ModuleType

        p01_root = s.project_01_path / "src"
        if not p01_root.exists():
            raise RuntimeError(f"Project 01 not found at {s.project_01_path}")

        # We prepend project 01's root to sys.path so its absolute imports
        # like `from .shared.corpus import ...` resolve. To avoid colliding
        # with the harness's own `src` package, we install a dedicated
        # finder for project 01's modules under the alias `p01src`.
        if str(s.project_01_path) not in _sys.path:
            _sys.path.insert(0, str(s.project_01_path))
        # Remove any cached `src.*` modules that came from the harness
        # before falling through to project 01. This is safe because the
        # harness has already finished its imports by the time we run.
        # (We restore them after.)
        saved: dict[str, ModuleType] = {}
        for k in list(_sys.modules):
            if k == "src" or k.startswith("src."):
                # Don't touch our own package — but make sure project 01
                # gets its own fresh import. We achieve this by temporarily
                # renaming our `src` to `_harness_src`.
                pass
        # The most reliable approach: drop any 'src' from sys.modules so
        # project 01's `src` loads fresh from its own directory. Save and
        # restore the harness's modules around this.
        harness_src_modules = {
            k: v for k, v in _sys.modules.items()
            if k == "src" or k.startswith("src.")
        }
        for k in harness_src_modules:
            del _sys.modules[k]
        try:
            from importlib import import_module
            corpus_mod = import_module("src.shared.corpus")
            self._contracts = corpus_mod.load_sample()
            self._mod = import_module(f"src.{self.backend}_impl.{self.technique}")
            # Hold strong references so a later sys.modules cleanup
            # doesn't garbage-collect them.
            self._project01_modules = {
                k: v for k, v in _sys.modules.items()
                if k == "src" or k.startswith("src.")
            }
        finally:
            # Drop project 01's modules and restore the harness's
            # modules so the rest of the harness keeps working.
            p01_modules = {
                k: v for k, v in _sys.modules.items()
                if (k == "src" or k.startswith("src.")) and k not in harness_src_modules
            }
            for k in p01_modules:
                del _sys.modules[k]
            for k, v in harness_src_modules.items():
                _sys.modules[k] = v

    def run(self, question: str, contract_doc_id: str) -> RunOutput:
        self._ensure_loaded()
        single = [c for c in self._contracts if c.doc_id == contract_doc_id]
        if not single:
            raise ValueError(f"contract {contract_doc_id!r} not in project 01 sample corpus")
        # Each technique exposes a `run(question, contracts, top_k=...)` -> RagResult
        result = self._mod.run(question, single, top_k=4)
        retrieved = getattr(result, "retrieved", [])
        return RunOutput(
            answer=getattr(result, "answer", str(result)),
            retrieved_chunk_ids=[c.chunk_id for c in retrieved],
            retrieved_doc_ids=[c.doc_id for c in retrieved],
            retrieved_text=[c.text for c in retrieved],
            raw_meta={
                "technique": getattr(result, "technique", self.name),
                "backend": self.backend,
            },
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def known_suts() -> dict[str, Sut]:
    """Return the SUTs the harness can evaluate today.

    Adding a new SUT = appending to this dict. Future SUTs from the portfolio
    (project 02 agentic-rag, project 03 graphrag) plug in identically.
    """
    return {
        "project01.langchain.naive": _Project01Sut("langchain", "naive"),
        "project01.langchain.hybrid": _Project01Sut("langchain", "hybrid"),
        "project01.llamaindex.naive": _Project01Sut("llamaindex", "naive"),
    }


def get_sut(name: str) -> Sut:
    suts = known_suts()
    if name not in suts:
        raise KeyError(f"Unknown SUT: {name}. Known: {sorted(suts)}")
    return suts[name]
