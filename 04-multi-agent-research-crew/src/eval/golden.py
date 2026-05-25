"""Golden eval set: real public companies + seller offerings.

Each entry is a ResearchRequest the crew runs end-to-end. The rubric
(eval/rubric.py) then scores the resulting AccountBriefing on 6 axes.

We picked real US public companies so SEC EDGAR returns real data, paired
with realistic seller-offering scenarios that exercise different signal
types:
  - tech earnings/AI strategy   (Microsoft + AI observability)
  - retail/customer experience  (Costco + customer analytics)
  - healthcare/regulatory       (Pfizer + clinical-trial software)
  - energy/transition           (NextEra + grid-management software)
  - financial services/risk     (JPMorgan + AML compliance)

Different industries mean the agents can't memorize a single template.
"""
from __future__ import annotations

from ..models import ResearchRequest


GOLDEN: list[ResearchRequest] = [
    ResearchRequest(
        company_name="Microsoft Corporation",
        company_domain="microsoft.com",
        seller_offering=(
            "AI observability and evaluation platform that monitors LLM "
            "applications in production, surfaces hallucinations and drift, "
            "and integrates with Azure ML"
        ),
        meeting_context="Discovery call with VP of AI Platform Engineering",
    ),
    ResearchRequest(
        company_name="Costco Wholesale Corporation",
        company_domain="costco.com",
        seller_offering=(
            "Customer-experience analytics platform for in-store and "
            "membership-renewal optimization, real-time NPS tracking, and "
            "personalized renewal offers"
        ),
        meeting_context="Executive briefing with Chief Membership Officer",
    ),
    ResearchRequest(
        company_name="Pfizer Inc.",
        company_domain="pfizer.com",
        seller_offering=(
            "Clinical-trial operations platform that automates protocol "
            "deviation tracking, site monitoring, and FDA submission "
            "evidence packaging"
        ),
        meeting_context="Product evaluation by Head of Clinical Operations",
    ),
    ResearchRequest(
        company_name="NextEra Energy Inc.",
        company_domain="nexteraenergy.com",
        seller_offering=(
            "Grid-edge optimization software for utility-scale renewable "
            "and battery-storage portfolios, with predictive maintenance "
            "and real-time dispatch"
        ),
        meeting_context="RFP response for grid-management refresh",
    ),
    ResearchRequest(
        company_name="JPMorgan Chase & Co.",
        company_domain="jpmorganchase.com",
        seller_offering=(
            "AML transaction-monitoring platform with explainable AI for "
            "false-positive reduction and regulator-ready audit trails"
        ),
        meeting_context="POC scoping with Head of Financial Crimes Compliance",
    ),
]


def load_golden() -> list[ResearchRequest]:
    return list(GOLDEN)
