"""Corpus loader for biomedical abstracts.

Two modes:
  - Default: a built-in synthetic sample (8 abstracts, fake PMIDs, clearly
    labeled `[SYNTHETIC]`). Lets the CLI run in seconds with zero network and
    zero risk of leaking misleading clinical content into tests.
  - --full: live PubMed fetch via NCBI E-utilities (esearch + efetch). Free,
    license-clear, rate-limited (3 req/s without an API key, 10 with one).

Each item is an Abstract with `pmid`, `title`, `abstract`, `journal`, `year`,
and `topics` (list of canonical tags used by the eval harness as ground truth).
"""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests


@dataclass
class Abstract:
    pmid: str
    title: str
    abstract: str
    journal: str = ""
    year: str = ""
    topics: list[str] = field(default_factory=list)  # canonical tags for eval

    @property
    def text(self) -> str:
        """Combined text used for embedding/retrieval."""
        return f"{self.title}\n\n{self.abstract}"

    @property
    def display(self) -> str:
        venue = f"{self.journal} {self.year}".strip()
        head = f"[PMID {self.pmid}]" + (f" ({venue})" if venue else "")
        return f"{head} {self.title}\n{self.abstract}"


# ---------------------------------------------------------------------------
# Built-in synthetic sample. Fake PMIDs (9 digits, prefixed 99...) so they
# can never collide with real PubMed records. Topic tags drive the eval.
# ---------------------------------------------------------------------------

_SAMPLE: list[Abstract] = [
    Abstract(
        pmid="990000001",
        title="[SYNTHETIC] Cardiovascular safety of SGLT2 inhibitors in T2DM: a meta-analysis",
        abstract=(
            "Background: SGLT2 inhibitors are recommended for type 2 diabetes mellitus "
            "with cardiovascular risk. We pooled data from 12 randomized trials "
            "(n=78,452). Results: SGLT2 inhibitors reduced major adverse cardiovascular "
            "events (MACE) by 14% (HR 0.86, 95% CI 0.81-0.92). Hospitalization for heart "
            "failure dropped 31%. Diabetic ketoacidosis occurred in 0.18% vs 0.06% on "
            "placebo. Conclusion: SGLT2 inhibitors confer cardiovascular benefit but "
            "clinicians must monitor for ketoacidosis."
        ),
        journal="J Synth Cardiol",
        year="2024",
        topics=["sglt2", "cardiovascular", "diabetes", "ketoacidosis"],
    ),
    Abstract(
        pmid="990000002",
        title="[SYNTHETIC] Empagliflozin and acute kidney injury: real-world cohort",
        abstract=(
            "Objective: Quantify acute kidney injury (AKI) risk with empagliflozin in "
            "routine care. Methods: Retrospective cohort, n=24,118 new users matched "
            "to DPP-4 inhibitor controls. Results: AKI incidence was 8.4 per 1000 "
            "person-years on empagliflozin vs 11.1 in controls (HR 0.76, 95% CI "
            "0.66-0.88). Volume depletion was the most common precipitant. "
            "Conclusion: Empagliflozin did not increase AKI in this cohort."
        ),
        journal="Synth Nephrol",
        year="2023",
        topics=["sglt2", "empagliflozin", "kidney"],
    ),
    Abstract(
        pmid="990000003",
        title="[SYNTHETIC] GLP-1 receptor agonists and pancreatitis risk: 10-year review",
        abstract=(
            "We systematically reviewed 47 studies on GLP-1 receptor agonists and acute "
            "pancreatitis. Pooled relative risk was 1.05 (95% CI 0.92-1.21), not "
            "statistically significant. Subgroup analysis showed no signal for "
            "semaglutide, liraglutide, or dulaglutide individually. Pancreatic cancer "
            "risk was likewise null at 5-year follow-up. We conclude prior FDA labeling "
            "concerns are not supported by current evidence."
        ),
        journal="Synth Endocrinol",
        year="2025",
        topics=["glp1", "pancreatitis", "semaglutide", "safety"],
    ),
    Abstract(
        pmid="990000004",
        title="[SYNTHETIC] Semaglutide for weight management: 68-week randomized trial",
        abstract=(
            "Methods: 1,961 adults with obesity (BMI >=30) randomized 2:1 to semaglutide "
            "2.4 mg weekly vs placebo, alongside lifestyle intervention. Results: Mean "
            "weight change was -14.9% with semaglutide vs -2.4% with placebo (p<0.001). "
            "Gastrointestinal adverse events occurred in 74.2% on semaglutide vs 47.9% "
            "on placebo, mostly mild-moderate nausea. Discontinuation for adverse events "
            "was 7.0% vs 3.1%."
        ),
        journal="Synth Med",
        year="2024",
        topics=["glp1", "semaglutide", "obesity", "weight"],
    ),
    Abstract(
        pmid="990000005",
        title="[SYNTHETIC] Direct oral anticoagulants vs warfarin in atrial fibrillation",
        abstract=(
            "Network meta-analysis of 7 trials (n=71,683) comparing apixaban, "
            "dabigatran, edoxaban, rivaroxaban, and warfarin in non-valvular atrial "
            "fibrillation. Apixaban showed the most favorable composite of stroke "
            "prevention and major bleeding (HR vs warfarin 0.79, 95% CI 0.66-0.94). "
            "All DOACs reduced intracranial hemorrhage versus warfarin. Conclusion: "
            "DOACs, particularly apixaban, should be preferred over warfarin for most "
            "non-valvular AF patients."
        ),
        journal="Synth Hematol",
        year="2023",
        topics=["doac", "apixaban", "atrial-fibrillation", "bleeding"],
    ),
    Abstract(
        pmid="990000006",
        title="[SYNTHETIC] Apixaban dose reduction in elderly: post-hoc analysis",
        abstract=(
            "Background: Apixaban requires dose reduction (2.5 mg BID) when 2 of 3 "
            "criteria are met: age >=80, weight <=60 kg, serum creatinine >=1.5 mg/dL. "
            "We analyzed 18,201 patients aged >=75. Results: 22% met criteria for "
            "reduced dose. Reduced-dose patients had similar stroke rates and lower "
            "major bleeding (HR 0.71, 95% CI 0.55-0.91). Conclusion: Label-adherent "
            "dose reduction maintains efficacy and reduces bleeding."
        ),
        journal="Synth Geriatr",
        year="2024",
        topics=["doac", "apixaban", "elderly", "dosing", "bleeding"],
    ),
    Abstract(
        pmid="990000007",
        title="[SYNTHETIC] Statin-induced myopathy: incidence and management",
        abstract=(
            "Across 32 trials (n=187,512), statin-associated muscle symptoms occurred "
            "in 9.1% on statins vs 8.2% on placebo, with a 0.9% absolute excess. "
            "Severe rhabdomyolysis was rare (0.04 per 1000 person-years). Switching "
            "statins or alternate-day dosing resolved symptoms in 72% of cases. "
            "Coenzyme Q10 supplementation showed no benefit in randomized data."
        ),
        journal="Synth Lipidol",
        year="2024",
        topics=["statin", "myopathy", "rhabdomyolysis"],
    ),
    Abstract(
        pmid="990000008",
        title="[SYNTHETIC] PCSK9 inhibitors vs ezetimibe add-on after maximal statin",
        abstract=(
            "Randomized trial (n=4,212) of evolocumab vs ezetimibe added to maximally "
            "tolerated statin in established atherosclerotic disease. LDL-C reduction "
            "was 59% with evolocumab vs 19% with ezetimibe. MACE at 2 years: 7.4% vs "
            "9.1% (HR 0.81, 95% CI 0.69-0.95). Injection-site reactions were the most "
            "common evolocumab adverse event."
        ),
        journal="Synth Cardiol",
        year="2025",
        topics=["pcsk9", "evolocumab", "ezetimibe", "statin", "ldl"],
    ),
]


def load_sample() -> list[Abstract]:
    return list(_SAMPLE)


# ---------------------------------------------------------------------------
# Live PubMed fetch via NCBI E-utilities.
# Docs: https://www.ncbi.nlm.nih.gov/books/NBK25501/
# ---------------------------------------------------------------------------

_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def _ncbi_params() -> dict[str, str]:
    p: dict[str, str] = {}
    if k := os.getenv("NCBI_API_KEY"):
        p["api_key"] = k
    if t := os.getenv("NCBI_TOOL"):
        p["tool"] = t
    if e := os.getenv("NCBI_EMAIL"):
        p["email"] = e
    return p


def search_pubmed(query: str, *, retmax: int = 20) -> list[str]:
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": str(retmax),
        **_ncbi_params(),
    }
    r = requests.get(_ESEARCH, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    return list(data.get("esearchresult", {}).get("idlist", []))


def fetch_abstracts(pmids: list[str]) -> list[Abstract]:
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "rettype": "abstract",
        **_ncbi_params(),
    }
    # Be polite. NCBI: 3 req/s w/o key, 10 w/ key.
    time.sleep(0.34 if "api_key" not in params else 0.11)
    r = requests.get(_EFETCH, params=params, timeout=30)
    r.raise_for_status()
    return _parse_pubmed_xml(r.text)


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _parse_pubmed_xml(xml_text: str) -> list[Abstract]:
    out: list[Abstract] = []
    root = ET.fromstring(xml_text)
    for art in root.findall(".//PubmedArticle"):
        pmid = _text(art.find(".//PMID")) or ""
        title = _text(art.find(".//ArticleTitle"))
        # AbstractText may have multiple sections (Background/Methods/etc.)
        sections = []
        for at in art.findall(".//Abstract/AbstractText"):
            label = at.attrib.get("Label")
            txt = _text(at)
            if not txt:
                continue
            sections.append(f"{label}: {txt}" if label else txt)
        abstract = " ".join(sections)
        journal = _text(art.find(".//Journal/Title"))
        year = _text(art.find(".//JournalIssue/PubDate/Year"))
        if not abstract:
            continue
        out.append(
            Abstract(
                pmid=pmid,
                title=title,
                abstract=abstract,
                journal=journal,
                year=year,
                topics=[],
            )
        )
    return out


def load_full(query: str, retmax: int = 20) -> list[Abstract]:
    pmids = search_pubmed(query, retmax=retmax)
    return fetch_abstracts(pmids)


def load_corpus(
    *, full: bool = False, query: str | None = None, retmax: int = 20
) -> list[Abstract]:
    if full:
        if not query:
            raise RuntimeError("--full requires --query for the PubMed search.")
        return load_full(query, retmax=retmax)
    return load_sample()
