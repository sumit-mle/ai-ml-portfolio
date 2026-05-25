"""LLM and embedding helpers + system prompts for graph extraction and Q/A."""
from __future__ import annotations

from .config import get_settings


# ---------------------------------------------------------------------------
# Generator prompts (graph_rag and vector_rag share these)
# ---------------------------------------------------------------------------

GRAPH_RAG_SYSTEM = (
    "You are an M&A due-diligence analyst. You answer questions about companies, "
    "executives, subsidiaries, ownership, board overlaps, and supply-chain risks "
    "using ONLY the provided graph context (entities, relationships, and filing "
    "excerpts). Cite filing accession numbers in square brackets, e.g. [0001234567-24-000001]. "
    "If the graph does not contain the answer, say 'Not found in the available filings.'"
)


VECTOR_RAG_SYSTEM = (
    "You are an M&A due-diligence analyst. Answer using ONLY the provided filing "
    "excerpts. Cite filing accession numbers in square brackets, e.g. "
    "[0001234567-24-000001]. If the excerpts do not contain the answer, say "
    "'Not found in the available filings.'"
)


# ---------------------------------------------------------------------------
# Extraction prompt — produces structured Entities + Relations from filing text
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM = (
    "You extract a knowledge graph from M&A-relevant disclosures (10-K, 10-Q, "
    "8-K, S-1, DEF 14A). Read the text and emit ENTITIES and RELATIONSHIPS in "
    "the exact JSON schema requested.\n\n"
    "ENTITY TYPES: Company, Person, Location\n\n"
    "RELATIONSHIP TYPES (be precise — use only these):\n"
    "  - HAS_SUBSIDIARY     (Company -> Company)\n"
    "  - OWNS_STAKE_IN      (Company -> Company; include 'pct' if disclosed)\n"
    "  - ACQUIRED           (Company -> Company; include 'year' if known)\n"
    "  - EXECUTIVE_OF       (Person -> Company; include 'role' e.g. CEO, CFO)\n"
    "  - BOARD_MEMBER_OF    (Person -> Company)\n"
    "  - FORMER_EXECUTIVE_OF (Person -> Company; include 'role')\n"
    "  - SUPPLIES           (Company -> Company)\n"
    "  - PARTNER_WITH       (Company -> Company)\n"
    "  - ADVISED            (Company -> Company)\n"
    "  - HEADQUARTERED_IN   (Company -> Location)\n"
    "  - SANCTIONED_BY      (Company -> Location/Authority)\n\n"
    "STRICT RULES:\n"
    "1. Use the canonical legal name when given (e.g., 'Apple Inc.' not 'Apple', "
    "'Tesla, Inc.' not 'TESLA, INC.'). Match the exact casing the filing uses.\n"
    "2. Don't invent relationships not stated in the text.\n"
    "3. If the text doesn't mention a clear relationship type from the list above, omit it.\n"
    "4. ENTITY NAMES MUST BE PROPER NOUNS:\n"
    "   - Person.name = a real human name (first + last). NEVER a job title like 'CEO' or "
    "     'Senior Vice President of Software Engineering'.\n"
    "   - Company.name = a legal entity name. NEVER a generic word like 'Company' or 'Board'.\n"
    "   - Location.name = a real place or authority (e.g., 'OFAC', 'Cupertino, California').\n"
    "5. For EXECUTIVE_OF and BOARD_MEMBER_OF, the source MUST be a real person's full name. "
    "If you only see a title (e.g., 'the Senior Vice President'), skip the relation.\n"
    "6. Capture every named person and their role explicitly (CEO, CFO, COO, Chair, Director).\n"
    "7. Return ONLY valid JSON matching the schema. No prose."
)


def chat_model_name() -> str:
    return get_settings().gen_model


def extract_model_name() -> str:
    return get_settings().extract_model


def embed_model_name() -> str:
    return get_settings().embed_model
