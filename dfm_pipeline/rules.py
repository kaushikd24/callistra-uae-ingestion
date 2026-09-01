"""
Title-based canonical_doc_type classification for DFM.

DFM's headlines are much more templated than Nasdaq Dubai's: a 2,500-record
sample spanning Oct 2025-Sep 2026 (pulled via full pagination) contained only
297 unique normalized headlines. Rules below give 100% coverage on that
sample (every record either matches a rule or falls through cleanly to the
resources[].type fallback, which itself has a value for every record —
DFM's disclosures always carry at least one PDF resource, unlike Nasdaq
Dubai's HTML-body-only path).

Rules are ordered; first match wins. Digits are collapsed to "#" before
matching, same convention as adx_pipeline/mapping.py and
nasdaq_dubai_pipeline/rules.py.
"""
from __future__ import annotations

import re

RULES: list[tuple[str, str]] = [
    (r'\bresults? of bo\.?d\.? meeting\b', 'board_meeting'),
    (r'\bbo\.?d\.? meeting\b', 'board_meeting'),
    (r'\bboard decisions by passing\b', 'board_meeting'),
    (r'\bresults? of board decisions by passing\b', 'board_meeting'),
    (r'\bpostponing bo\.?d\.? meeting\b', 'board_meeting'),
    (r'\bpress release\b', 'press_release'),
    (r'\bnotification from the company\b', 'general_disclosure'),
    (r'\bclarification from the company\b', 'general_disclosure'),
    (r'\bpost share buyback announcement\b', 'corporate_action'),
    (r'\bshare buyback\b', 'corporate_action'),
    (r'\btreasury shares?\b', 'corporate_action'),
    (r'\bresolutions? of (the )?general assembly\b', 'shareholder_meeting'),
    (r'\binvitation of (extraordinary )?general assembly\b', 'shareholder_meeting'),
    (r'\bpostponing general assembly\b', 'shareholder_meeting'),
    (r'\b(annual|extraordinary|general) assembly\b', 'shareholder_meeting'),
    (r'\begm\b|\bagm\b', 'shareholder_meeting'),
    (r'\bresults? of earnings call\b', 'earnings_call_update'),
    (r'\bearnings call\b', 'earnings_call_update'),
    (r'\bfinancial statements? for\b', 'financial_results'),
    (r'\binterim financial statements?\b', 'financial_results'),
    (r'\bintegrated report\b', 'annual_report'),
    (r'\bdetailed analysis accumulated losses\b', 'regulatory_compliance'),
    (r'\baccumulated losses\b', 'regulatory_compliance'),
    (r'\bpreliminary financial results\b', 'financial_results'),
    (r'\bnominees for board of directors\b', 'management_change'),
    (r'\bnomination(s)? for (the )?bo\.?d\.?\b', 'management_change'),
    (r'\bnomination.*board of director', 'management_change'),
    (r'\bpress release.*financial results\b', 'financial_results'),
    (r'\bpress release on financial results\b', 'financial_results'),
    (r'\bsupplementary disclosure\b', 'general_disclosure'),
    (r'\blawsuit disclosure\b', 'litigation_regulatory_action'),
    (r'\blitigation\b', 'litigation_regulatory_action'),
    (r'\bdisclosure of material information\b', 'general_disclosure'),
    (r'\bmaterial information disclosure\b', 'general_disclosure'),
    (r'\bresignation of bo\.?d\.? member\b', 'management_change'),
    (r'\bresignation of\b', 'management_change'),
    (r'\bappointment of\b', 'management_change'),
    (r'\boperational and financial results\b', 'financial_results'),
    (r'\bunusual trading activit', 'general_disclosure'),
    (r'\bmanagement discussion and analysis\b', 'financial_results'),
    (r'\btranscript of\b.*\bconference\b', 'earnings_transcript'),
    (r'\btranscript of the analysts conference\b', 'earnings_transcript'),
    (r'\bpresentation of\b.*\bconference\b', 'investor_presentation'),
    (r'\bcredit rating\b', 'general_disclosure'),
    (r'\bcma renewal\b', 'corporate_action'),
    (r'\bvoluntary offer\b', 'mna_restructuring'),
    (r'\bmerger|acquisition|disposal|divestment\b', 'mna_restructuring'),
    (r'\bdividend\b', 'corporate_action'),
    (r'\bcapital increase\b|\brights issue\b', 'fundraising'),
    (r'\bprospectus\b', 'fundraising'),
]

_COMPILED = [(re.compile(pattern, re.IGNORECASE), canon) for pattern, canon in RULES]


def classify(headline: str, resource_type: str | None) -> str:
    """canonical_doc_type from headline text, falling back to resources[].type."""
    normalized = re.sub(r"\d+", "#", (headline or "").strip())
    for pattern, canon in _COMPILED:
        if pattern.search(normalized):
            return canon

    if resource_type == "financial_reports":
        return "financial_results"
    if resource_type == "general_meetings":
        return "shareholder_meeting"
    if resource_type == "integrated_reports":
        return "annual_report"
    if resource_type == "news":
        return "general_disclosure"
    return "other"
