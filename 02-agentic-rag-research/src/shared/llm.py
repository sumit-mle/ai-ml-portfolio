"""LLM and embedding helpers, framework-agnostic.

The agent uses LangChain's ChatOpenAI for the planner/generator/reflector calls
(easier prompt composition with structured output) and LlamaIndex for the
retriever (cleaner node abstractions). Both route through these helpers so the
model choice lives in one place.
"""
from __future__ import annotations

import os


def get_chat_model_name() -> str:
    return os.getenv("GEN_MODEL", "gpt-4o-mini")


def get_embed_model_name() -> str:
    return os.getenv("EMBED_MODEL", "text-embedding-3-small")


def require_openai_key() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

GENERATOR_SYSTEM = (
    "You are a medical-affairs literature assistant. Your readers are pharma "
    "medical-affairs scientists summarizing evidence for healthcare professionals. "
    "Use ONLY the provided abstracts. Describe what the evidence reports — including "
    "trial results, effect sizes, confidence intervals, and null findings — even "
    "when the question is phrased in clinical terms. You are reporting evidence, "
    "not giving clinical advice. "
    "Quote the exact phrase(s) you rely on. Cite every factual claim with the "
    "PMID in square brackets, e.g. [PMID 12345678]. "
    "Only respond 'The retrieved literature does not address this question.' when "
    "NONE of the retrieved abstracts is on-topic for the question. If at least one "
    "abstract is on-topic, summarize its findings even if they are inconclusive."
)


REFLECTOR_SYSTEM = (
    "You are a strict reviewer of medical-literature answers. You will be given "
    "a question, a set of retrieved abstracts (with PMIDs), and a draft answer. "
    "Score the answer on three criteria, each 0.0 to 1.0:\n"
    "  - grounded: every claim is supported by a quoted span from the abstracts\n"
    "  - cited: every factual claim has a [PMID] citation\n"
    "  - complete: the answer addresses the question fully\n"
    "If grounded < 0.85 or cited < 0.85 or complete < 0.7, set needs_more_evidence "
    "to true and propose ONE focused follow-up query to fill the gap. "
    "Return JSON only with keys: grounded, cited, complete, needs_more_evidence, "
    "follow_up_query, critique."
)


PLANNER_SYSTEM = (
    "You are a search-query planner for medical-affairs literature review. "
    "Given a question, return ONE concise PubMed-style search query (no boolean "
    "operators required, just keywords). Prefer drug INN names over brand names. "
    "Return only the query string, no explanation."
)


def build_generator_user_prompt(question: str, contexts: list[str]) -> str:
    """contexts: pre-formatted blocks like '[PMID 12345] Title — abstract...'"""
    joined = "\n\n---\n\n".join(contexts) if contexts else "(no abstracts retrieved)"
    return (
        f"Question: {question}\n\n"
        f"Retrieved abstracts:\n{joined}\n\n"
        "Answer (quote spans and cite PMIDs):"
    )


def build_reflector_user_prompt(
    question: str, contexts: list[str], draft: str
) -> str:
    joined = "\n\n---\n\n".join(contexts) if contexts else "(no abstracts retrieved)"
    return (
        f"Question: {question}\n\n"
        f"Retrieved abstracts:\n{joined}\n\n"
        f"Draft answer:\n{draft}\n\n"
        "Return JSON only."
    )
