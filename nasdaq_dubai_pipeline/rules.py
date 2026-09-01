"""
Title-based canonical_doc_type classification for Nasdaq Dubai.

Unlike ADX (which ships a clean subCategoryNameEn taxonomy), Nasdaq Dubai's
API gives almost no structured filing-type signal — the `candi` listing has
no type at all, and the detail record's `resources[].type` only takes 3
values (financial_reports / news / absent). So classification here leans on
the headline text itself, which follows LSEG/RNS-style templates.

Ruleset built from the full available corpus (18,201 disclosures, Nov 2005 -
present, pulled via full pagination of /candi/v1/candi) at the time this was
written. Coverage on that corpus:
  - ~69% matched directly by a rule below
  - ~7.6% are headlines that are literally just the issuer's name (e.g. the
    entire headline is "Hikma Pharmaceuticals PLC") — no title signal exists
    to classify these; falls through to the resource-type/other fallback
  - ~23.5% remainder falls through to 'other' (user declined an offline
    GPT-nano classification pass for this long tail — revisit if warranted)

Rules are ordered; first match wins. Digits in the headline are collapsed to
"#" before matching (so date/reference numbers don't need to be enumerated).
"""
from __future__ import annotations

import re

# (regex, canonical_doc_type) — first match wins.
RULES: list[tuple[str, str]] = [
    (r'\bcircular no\.?:?\s*#?/?#?\s*-\s*margin parameters?\b', 'regulatory_general'),
    (r'\bnotice no\.?:?\s*#?/?#?\s*-\s*margin parameters?\b', 'regulatory_general'),
    (r'\bnotice no\.?:?\s*#?/?#?\s*-', 'regulatory_general'),
    (r'\bcircular no\.?:?\s*#?/?#?', 'regulatory_general'),
    (r'\badmission to trading notice\b', 'fundraising'),
    (r'\bblock listing\b', 'regulatory_compliance'),
    (r'\btransaction(s)? in own shares\b', 'corporate_action'),
    (r'\bpurchase of (treasury|own) shares?\b', 'corporate_action'),
    (r'\btreasury shares?\b', 'corporate_action'),
    (r'\bconversion of securities\b', 'corporate_action'),
    (r'\bbuyback\b', 'corporate_action'),
    (r'\bperiodic (distribution|profit distribution)\b', 'corporate_action'),
    (r'\bcoupon payment\b', 'corporate_action'),
    (r'\bdividend\b', 'corporate_action'),
    (r'\brights issue\b', 'corporate_action'),
    (r'\bbond repurchase\b', 'corporate_action'),
    (r'\bpartial return of capital\b', 'corporate_action'),
    (r'\bfrn variable rate fix\b', 'general_disclosure'),
    (r'\bnet asset values?\(?s?\)?\b', 'financial_results'),
    (r'\bmonthly shareholder report\b', 'financial_results'),
    (r'\btrading statement\b', 'financial_results'),
    (r'\binterim management statement\b', 'financial_results'),
    (r'\bupcoming results calendar\b', 'earnings_call_update'),
    (r'\bannual financial report\b', 'annual_report'),
    (r'\bannual report\b', 'annual_report'),
    (r'\bhalf[\s-]yearly results\b', 'financial_results'),
    (r'\b(annual|final) results?\b', 'financial_results'),
    (r'\bresults?\b.*\b(period|month|year|quarter)\b', 'financial_results'),
    (r'\b(half|full)[\s-]year results\b', 'financial_results'),
    (r'\binterim report\b', 'financial_results'),
    (r'\bfinancial statements?\b', 'financial_results'),
    (r'\bpreliminary results\b', 'financial_results'),
    (r'\bmonthly update\b', 'business_updates'),
    (r'\badds (usd|aed)?\s?#.*(backlog|contract|order)', 'business_updates'),
    (r'\bannouncement for your information\b', 'general_disclosure'),
    (r'\bnational and foreign ownership update\b', 'general_disclosure'),
    (r'\bdirector/pdmr (shareholding|holding)\b', 'general_disclosure'),
    (r'\bdirector declaration\b', 'general_disclosure'),
    (r'\bholding\(?s?\)? in company\b', 'general_disclosure'),
    (r'\btotal voting rights\b', 'general_disclosure'),
    (r'\bvoting rights and capital\b', 'general_disclosure'),
    (r'\bdealing in securities\b', 'general_disclosure'),
    (r'\bexecutive officer(s)? files? (form|ownership)\b', 'general_disclosure'),
    (r'\bdirectors? files? (form|ownership)\b', 'general_disclosure'),
    (r'\bfiles form \d', 'general_disclosure'),
    (r'\bfiles? ownership report', 'general_disclosure'),
    (r'\bnotification of transaction', 'general_disclosure'),
    (r'\bchanges? in interest of substantial shareholders?\b', 'general_disclosure'),
    (r'\bshareholding disclosure\b', 'general_disclosure'),
    (r'\bsec filings? relating to\b', 'general_disclosure'),
    (r'\btranslation of .* regulatory disclosure\b', 'general_disclosure'),
    (r'\bdisclosures? under\s*reg', 'general_disclosure'),
    (r'\bbond xs#', 'general_disclosure'),
    (r'-\s*xs#', 'general_disclosure'),
    (r'-disclosure\b', 'general_disclosure'),
    (r'\bgeneral disclosure\b', 'general_disclosure'),
    (r'\btransparency (report|reporting)\b', 'regulatory_compliance'),
    (r'\bcompliance with model code\b', 'regulatory_compliance'),
    (r'\bannual information update\b', 'regulatory_compliance'),
    (r'\bcorporate governance\b', 'regulatory_compliance'),
    (r'\bpress release\b', 'press_release'),
    (r'\bmedia release\b', 'press_release'),
    (r'\bnotice of annual general meeting\b', 'shareholder_meeting'),
    (r'\b(annual|extraordinary|general) meeting\b', 'shareholder_meeting'),
    (r'\begm\b|\bagm\b', 'shareholder_meeting'),
    (r'\bboard of directors\b', 'board_meeting'),
    (r'\bdirectorate change\b', 'management_change'),
    (r'\b(appointment|resignation|retirement) of\b', 'management_change'),
    (r'\bchief (executive|financial) officer\b', 'management_change'),
    (r'\bmerger|acquisition|disposal|divestment\b', 'mna_restructuring'),
    (r'\bprospectus\b', 'fundraising'),
    (r'\bsukuk\b.*\bissu', 'fundraising'),
    (r'\bannounces?\b.*\bresults?\b', 'financial_results'),
    (r'\bq#\s*results?\b', 'financial_results'),
    (r'\bfinancials?\s*#', 'financial_results'),
    (r'\bform\s*#-k\b', 'financial_results'),
    (r'\bhalf yearly report\b', 'financial_results'),
    (r'\bfinancial (results|accounts)\b', 'financial_results'),
    (r'\bnotification of (directors?|major interests?)\b.*\bshares?\b', 'general_disclosure'),
    (r'\bannouncement on credit rating\b', 'general_disclosure'),
    (r'\bshare conversion\b', 'corporate_action'),
    (r'\bshare repurchase programme\b', 'corporate_action'),
    (r'\bprofit distribution\b', 'corporate_action'),
    (r'\binvitation to\b.*\bmeetings?\b', 'shareholder_meeting'),
    (r'\bdirector/\s*pdmr\b', 'general_disclosure'),
]

_COMPILED = [(re.compile(pattern), canon) for pattern, canon in RULES]

# Issuers that are the exchange/regulator itself, not a listed company —
# circulars/notices from these get entity_type='government'.
EXCHANGE_AND_REGULATOR_ISSUERS = {
    "nasdaq dubai",
    "dubai financial services authority",
}


def is_exchange_or_regulator(issuer: str | None) -> bool:
    return bool(issuer) and issuer.strip().lower() in EXCHANGE_AND_REGULATOR_ISSUERS


def classify(headline: str, issuer: str | None, resource_type: str | None) -> str:
    """canonical_doc_type from headline text, falling back to the coarse
    resources[].type signal from the detail record, then 'other'.

    Note: headlines that are just the bare issuer name (~7.6% of the corpus,
    e.g. headline == "Hikma Pharmaceuticals PLC") carry no title signal at
    all and fall straight through to the resource-type fallback below —
    there's nothing rule-based to extract from a company name alone.
    """
    normalized = re.sub(r"\d+", "#", (headline or "").strip().lower())
    for pattern, canon in _COMPILED:
        if pattern.search(normalized):
            return canon

    if resource_type == "financial_reports":
        return "financial_results"
    if resource_type == "news":
        return "general_disclosure"
    return "other"
