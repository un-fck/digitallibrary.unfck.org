"""Deterministic action-verb parser for UN resolution paragraphs.

Replaces the legacy LLM extraction (``mandates.paragraph_mandates``) with a
declarative, stdlib-only, fully unit-testable parser. Given the text of an
operative or preambular paragraph (plus a little structural context: its
``paragraph_type``, ``level``, ``prefix`` and the resolved *chapeau* action for
sub-items), it returns a structured action record:

    {
        "verb":            "Requests",              # verbatim leading surface form
        "normalized":      "request",               # legacy-compatible lemma
        "category":        "directive",             # observing|reinforcing|evaluative|deciding|directive
        "force":           3,                        # 0-5 ordinal (directive/deciding spine)
        "sentiment":       0,                        # +1 / 0 / -1
        "bindingness":     "hortatory",             # binding|hortatory|contextual
        "budget_relevant": True,
        "modifiers":       [{"kind": "repetition", "text": "also"}, ...],
        "compound":        False,
        "secondary_verbs": [ {...}, ... ],           # for "welcomes and endorses"
        "assignee":        {"verbatim": "...", "head_noun": "...",
                            "addressee_class": "secretary-general"},   # directive only
        "inherited":       False,                    # True for sub-items using chapeau verb
        "infinitive_verb": "strengthen",            # 'To strengthen ...' sub-items
        "context_marker":  None,                     # 'chapter_vii' for 'Acting under Chapter VII'
        "paragraph_type":  "operative",
    }

Design evidence: ``verb_research_empirical.md`` (corpus mining of the two
Postgres corpora) and ``verb_research_literature.md`` (sourced ~70-verb
taxonomy, DGACM lists, force ordering, Searle illocutionary spine).

The module is import-safe (no DB, no I/O at import) and ships a ``__main__``
self-test block (``python fulltext_verbs.py``) plus a tiny CLI
(``python fulltext_verbs.py "Requests the Secretary-General to report ..."``).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Category / dimension vocabularies
# ---------------------------------------------------------------------------

# The 5-category "spine" is kept for legacy compatibility (see literature
# report Part H): observing~assertive, evaluative~expressive,
# deciding~commissive/declarative, directive~directive, reinforcing~anaphora
# operator. Orthogonal dimensions (force, sentiment, bindingness,
# budget_relevant, addressee) are carried alongside per the report's critique.
OBSERVING = "observing"
REINFORCING = "reinforcing"
EVALUATIVE = "evaluative"
DECIDING = "deciding"
DIRECTIVE = "directive"

BINDING = "binding"
HORTATORY = "hortatory"
CONTEXTUAL = "contextual"


@dataclass(frozen=True)
class VerbEntry:
    """One verb lemma and its taxonomy dimensions."""

    normalized: str
    category: str
    operative: tuple[str, ...] = ()   # 3rd-person operative surface forms (lower)
    preambular: tuple[str, ...] = ()  # participial / adjectival surface forms (lower)
    force: int = 0
    sentiment: int = 0
    bindingness: str = CONTEXTUAL
    budget_relevant: bool = False
    addressee_expected: bool = False
    # carrier verbs get their polarity/normalized from a following object noun
    carrier: Optional[str] = None  # 'express' | 'note' | 'take_note'


def V(normalized, category, *, op=(), pre=(), force=0, sentiment=0,
      bindingness=CONTEXTUAL, budget=False, addressee=False, carrier=None):
    if isinstance(op, str):
        op = (op,)
    if isinstance(pre, str):
        pre = (pre,)
    return VerbEntry(normalized, category, tuple(op), tuple(pre), force,
                     sentiment, bindingness, budget, addressee, carrier)


# ---------------------------------------------------------------------------
# THE LEXICON
# ---------------------------------------------------------------------------
# Union of: legacy 71 normalized verbs, empirical top operative/preambular
# heads, and the literature master table. British spellings (-ise/-ising,
# emphasise, recognise, authorise, ...) are folded in as surface variants.

LEXICON: list[VerbEntry] = [
    # ----- observing (assertive / representative) -----
    V("note", OBSERVING, op=("notes",), pre=("noting",), carrier="note"),
    V("take note", OBSERVING, op=("takes note",), pre=("taking note",), carrier="take_note"),
    V("take into account", OBSERVING, op=("takes into account",),
      pre=("taking into account", "taking into consideration", "taking account")),
    V("observe", OBSERVING, op=("observes",), pre=("observing",)),
    V("recognize", OBSERVING, op=("recognizes", "recognises"),
      pre=("recognizing", "recognising")),
    V("acknowledge", OBSERVING, op=("acknowledges",), pre=("acknowledging",)),
    V("consider", OBSERVING, op=("considers",), pre=("considering",)),
    V("realize", OBSERVING, op=("realizes", "realises"), pre=("realizing", "realising")),
    V("bear in mind", OBSERVING, op=("bears in mind",), pre=("bearing in mind",)),
    # legacy folds the 'Having considered/examined/...' preambular form into 'consider'
    V("consider", OBSERVING,
      pre=("having considered", "having examined", "having heard", "having received",
           "having reviewed", "having noted", "having adopted", "having studied")),
    V("confirm", OBSERVING, op=("confirms",), pre=("confirming",)),
    # preambular-only adjectival stances
    V("be aware", OBSERVING, pre=("aware", "fully aware")),
    V("be conscious", OBSERVING, pre=("conscious", "fully conscious")),
    V("be mindful", OBSERVING, pre=("mindful",)),
    V("be cognizant", OBSERVING, pre=("cognizant",)),
    V("be convinced", OBSERVING, pre=("convinced", "fully convinced")),
    V("believe", OBSERVING, op=("believes",), pre=("believing",)),
    V("guided by", OBSERVING, pre=("guided",)),
    V("desirous", OBSERVING, pre=("desirous",)),

    # ----- reinforcing (anaphora / emphasis operator) -----
    V("reaffirm", REINFORCING, op=("reaffirms",), pre=("reaffirming",)),
    V("recall", REINFORCING, op=("recalls",), pre=("recalling",)),
    V("reiterate", REINFORCING, op=("reiterates",), pre=("reiterating",)),
    V("reconfirm", REINFORCING, op=("reconfirms",), pre=("reconfirming",)),
    V("affirm", REINFORCING, op=("affirms",), pre=("affirming",)),
    V("underline", REINFORCING, op=("underlines",), pre=("underlining",)),
    V("underscore", REINFORCING, op=("underscores",),
      pre=("underscoring",)),
    V("emphasize", REINFORCING, op=("emphasizes", "emphasises"),
      pre=("emphasizing", "emphasising")),
    V("stress", REINFORCING, op=("stresses",), pre=("stressing",)),
    V("highlight", REINFORCING, op=("highlights",), pre=("highlighting",)),
    V("remain", REINFORCING, op=("remains",), pre=("remaining",)),

    # ----- evaluative (expressive) -----
    V("welcome", EVALUATIVE, op=("welcomes",), pre=("welcoming",), sentiment=1),
    V("commend", EVALUATIVE, op=("commends",), pre=("commending",), sentiment=1),
    V("appreciate", EVALUATIVE, op=("appreciates",), pre=("appreciating",), sentiment=1),
    V("support", EVALUATIVE, op=("supports",), pre=("supporting",), force=1, sentiment=1),
    V("look forward", EVALUATIVE, op=("looks forward", "look forward"),
      pre=("looking forward",), sentiment=1),
    V("congratulate", EVALUATIVE, op=("congratulates",), pre=("congratulating",), sentiment=1),
    V("thank", EVALUATIVE, op=("thanks",), pre=("thanking",), sentiment=1),
    V("be concerned", EVALUATIVE,
      op=("are concerned", "is concerned", "are deeply concerned",
          "are gravely concerned", "are seriously concerned",
          "remains concerned", "remains deeply concerned", "remain concerned",
          "remain deeply concerned"),
      pre=("concerned", "gravely concerned", "deeply concerned", "seriously concerned",
           "deeply troubled", "troubled", "remaining concerned",
           "remaining deeply concerned"), sentiment=-1),
    V("be alarmed", EVALUATIVE, pre=("alarmed", "gravely alarmed", "deeply alarmed"),
      sentiment=-1),
    V("be disturbed", EVALUATIVE, pre=("disturbed", "deeply disturbed"), sentiment=-1),
    V("regret", EVALUATIVE, op=("regrets",), pre=("regretting",), sentiment=-1),
    V("deplore", EVALUATIVE, op=("deplores",), pre=("deploring",), sentiment=-1),
    V("condemn", EVALUATIVE, op=("condemns",), pre=("condemning",), sentiment=-1),
    V("denounce", EVALUATIVE, op=("denounces",), pre=("denouncing",), sentiment=-1),
    V("reject", EVALUATIVE, op=("rejects",), pre=("rejecting",), sentiment=-1),
    V("deprecate", EVALUATIVE, op=("deprecates",), pre=("deprecating",), sentiment=-1),
    V("trust", EVALUATIVE, op=("trusts",), pre=("trusting",), sentiment=1),
    # carrier: express -> polarity from object noun
    V("express", EVALUATIVE, op=("expresses",), pre=("expressing",), carrier="express"),

    # ----- deciding (commissive / declarative) -----
    V("decide", DECIDING, op=("decides",), pre=("deciding",), force=5,
      bindingness=BINDING, budget=True),
    V("resolve", DECIDING, op=("resolves",), pre=("resolving",), force=4),
    V("endorse", DECIDING, op=("endorses",), pre=("endorsing",), force=4,
      sentiment=1, bindingness=BINDING, budget=True),
    V("approve", DECIDING, op=("approves",), pre=("approving",), force=5,
      bindingness=BINDING, budget=True),
    V("adopt", DECIDING, op=("adopts",), pre=("adopting",), force=5,
      bindingness=BINDING, budget=True),
    V("appropriate", DECIDING, op=("appropriates",), force=5,
      bindingness=BINDING, budget=True),
    V("establish", DECIDING, op=("establishes",), pre=("establishing",), force=5,
      bindingness=BINDING, budget=True),
    V("authorize", DECIDING, op=("authorizes", "authorises"),
      pre=("authorizing", "authorising"), force=5, bindingness=BINDING, budget=True,
      addressee=True),
    V("determine", DECIDING, op=("determines",), force=5, bindingness=BINDING),
    V("renew", DECIDING, op=("renews",), pre=("renewing",), force=5,
      bindingness=BINDING, budget=True),
    V("proclaim", DECIDING, op=("proclaims",), pre=("proclaiming",), force=4),
    V("declare", DECIDING, op=("declares",), pre=("declaring",), force=4),
    V("agree", DECIDING, op=("agrees",), pre=("agreeing",), force=4),
    V("commit", DECIDING, op=("commits", "are determined", "is determined",
                              "commit ourselves", "commits itself", "undertakes"),
      pre=("committing",), force=3, sentiment=1),
    V("recommit", DECIDING, op=("recommits", "rededicate ourselves",
                                "recommit ourselves"), force=3, sentiment=1),
    V("pledge", DECIDING, op=("pledges",), pre=("pledging",), force=3, sentiment=1),
    V("continue", DECIDING, op=("continues",), pre=("continuing",), force=4),
    V("accept", DECIDING, op=("accepts",), pre=("accepting",), force=3),

    # ----- directive (directive speech act) -----
    V("request", DIRECTIVE, op=("requests",), pre=("requesting",), force=3,
      bindingness=HORTATORY, budget=True, addressee=True),
    V("call upon", DIRECTIVE, op=("calls upon",), pre=("calling upon",), force=3,
      bindingness=HORTATORY, addressee=True),
    V("call on", DIRECTIVE, op=("calls on",), pre=("calling on",), force=3,
      bindingness=HORTATORY, addressee=True),
    V("call for", DIRECTIVE, op=("calls for",), pre=("calling for",), force=3,
      bindingness=HORTATORY, addressee=False),
    V("urge", DIRECTIVE, op=("urges",), pre=("urging",), force=4,
      bindingness=HORTATORY, addressee=True),
    V("encourage", DIRECTIVE, op=("encourages",), pre=("encouraging",), force=1,
      sentiment=1, bindingness=HORTATORY, addressee=True),
    V("invite", DIRECTIVE, op=("invites",), pre=("inviting",), force=1,
      bindingness=HORTATORY, addressee=True),
    V("demand", DIRECTIVE, op=("demands",), pre=("demanding",), force=5,
      bindingness=HORTATORY, addressee=True),
    V("recommend", DIRECTIVE, op=("recommends",), pre=("recommending",), force=2,
      bindingness=HORTATORY, addressee=True),
    V("appeal", DIRECTIVE, op=("appeals",), pre=("appealing",), force=2,
      bindingness=HORTATORY, addressee=True),
    V("remind", DIRECTIVE, op=("reminds",), pre=("reminding",), force=2,
      bindingness=HORTATORY, addressee=True),
    V("direct", DIRECTIVE, op=("directs",), pre=("directing",), force=4,
      bindingness=BINDING, budget=True, addressee=True),
    V("suggest", DIRECTIVE, op=("suggests",), pre=("suggesting",), force=1,
      bindingness=HORTATORY, addressee=False),
    V("discourage", DIRECTIVE, op=("discourages",), pre=("discouraging",), force=1,
      sentiment=-1, bindingness=HORTATORY, addressee=True),
    V("ask", DIRECTIVE, op=("asks",), pre=("asking",), force=3,
      bindingness=HORTATORY, addressee=True),
]

# Verbs whose type/category legitimately shifts with context (empirical B.2).
# We assign the modal default above and flag these as ambiguous.
CONTEXT_DEPENDENT = frozenset(
    {"recognize", "note", "stress", "emphasize", "acknowledge", "consider",
     "affirm", "confirm", "must", "bear in mind"}
)

# ---------------------------------------------------------------------------
# Modifier vocabularies (stripped from the head, recorded as modifiers[])
# ---------------------------------------------------------------------------

# repetition / back-reference markers (DGACM: 'also' then 'further')
REPETITION_MODS = ("also", "further", "once again", "again")
# intensity adverbs
INTENSITY_MODS = (
    "strongly", "deeply", "gravely", "seriously", "firmly", "fully", "solemnly",
    "vehemently", "resolutely", "categorically", "unequivocally", "urgently",
    "particularly", "especially", "profoundly", "sincerely", "warmly",
    "strenuously", "unreservedly", "wholeheartedly",
)
# discourse connectives that sometimes lead a paragraph
CONNECTIVE_MODS = (
    "in this regard", "in this context", "in that regard", "therefore",
    "accordingly", "moreover", "furthermore", "similarly", "likewise",
    "nevertheless", "hereby", "consequently", "to that end", "to this end",
    "in particular",
)
ALL_LEADING_MODS = REPETITION_MODS + INTENSITY_MODS + CONNECTIVE_MODS


def _mod_kind(word: str) -> str:
    if word in REPETITION_MODS:
        return "repetition"
    if word in INTENSITY_MODS:
        return "intensity"
    return "connective"


# ---------------------------------------------------------------------------
# Carrier-verb object nouns (polarity of 'express X' / 'note with X')
# ---------------------------------------------------------------------------

POSITIVE_OBJECTS = (
    "appreciation", "satisfaction", "gratitude", "support", "confidence",
    "hope", "readiness", "willingness", "commitment", "conviction",
)
NEGATIVE_OBJECTS = (
    "concern", "grave concern", "deep concern", "serious concern", "alarm",
    "regret", "disappointment", "dismay", "indignation", "outrage", "anguish",
    "sadness", "worry", "apprehension",
)
NEUTRAL_OBJECTS = ("intention", "view", "opinion", "belief", "expectation")

# words that may sit between the carrier and its object noun
_CARRIER_FILLERS = ("its", "the", "his", "her", "their", "our", "a", "deep",
                    "grave", "serious", "profound", "great", "strong", "full",
                    "sincere", "utmost", "continued", "renewed")


# ---------------------------------------------------------------------------
# Assignee / addressee classification (built from empirical top-40 assignees)
# ---------------------------------------------------------------------------

# ordered (first match wins); each: (addressee_class, [keyword substrings])
ADDRESSEE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("secretary-general", ("secretary-general", "secretary general")),
    ("special_procedure", (
        "special rapporteur", "special representative", "special adviser",
        "special envoy", "working group", "independent expert", "mandate holder",
        "commission of inquiry", "fact-finding", "panel of experts",
        "monitoring group", "group of experts", "high commissioner",
    )),
    ("secretariat_entity", (
        "secretariat", "executive director", "executive secretary",
        "under-secretary-general", "office for", "office of", "office on",
        "department of", "ohchr", "unodc", "unctad", "un-women", "un women",
        "unep", "un-habitat", "unhcr", "undp", "unicef", "unfpa", "unrwa",
        "director-general", "director general", "focal point",
    )),
    ("member_states", (
        "member states", "all states", "states members", "the states",
        "governments", "government of", "all governments", "states parties",
        "member and observer states", "nation states", "administering powers",
    )),
    ("un_system", (
        "united nations system", "united nations development system",
        "un system", "development system", "funds and programmes",
        "specialized agencies", "specialised agencies", "un agencies",
        "united nations agencies", "un funds", "un programmes",
        "organizations of the united nations", "entities of the united nations",
        "united nations entities", "united nations bodies", "relevant organizations",
        "resident coordinator",
    )),
    ("un_body", (
        "general assembly", "security council", "economic and social council",
        "ecosoc", "human rights council", "president of the general assembly",
        "committee", "commission", "conference", "the council", "the assembly",
        "regional commissions", "subsidiary bod", "the board", "the bureau",
        "cstd", "cpc", "advisory committee", "the panel", "the group",
        "peacebuilding commission", "the forum", "the platform",
    )),
    ("ngo_other", (
        "civil society", "non-governmental organization", "non-governmental organisation",
        "ngo", "private sector", "international community", "international organization",
        "international organisation", "regional organization", "regional organisation",
        "financial institution", "stakeholder", "partner", "academia",
        "donor", "business", "parliament", "media", "youth", "women", "children",
        "indigenous", "employers", "trade union", "philanthrop",
    )),
]

_ARTICLE_PREFIXES = (
    "all relevant", "all other", "all the", "the relevant", "relevant",
    "all", "the", "other", "those", "these", "both", "each", "every",
    "concerned", "appropriate",
)


def classify_addressee(text: str) -> str:
    """Map an assignee verbatim span to an addressee class."""
    low = " " + text.lower().strip() + " "
    for cls, keywords in ADDRESSEE_RULES:
        for kw in keywords:
            if kw in low:
                return cls
    return "unclear"


# ---------------------------------------------------------------------------
# Match table (built once): surface -> VerbEntry, sorted longest-first
# ---------------------------------------------------------------------------

@dataclass
class _Surface:
    text: str          # lowercase surface
    entry: VerbEntry
    form: str          # 'operative' | 'preambular'
    regex: re.Pattern = field(compare=False, default=None)


# Lemmas whose bare infinitive/imperative base form appears at the start of
# enumerated sub-items in declarations ('Request the SG to ...', 'Invite ...',
# 'Recognize the need ...' inside the Pact for the Future). These are the sub-
# item's OWN verb, not the chapeau's. Only clear resolution verbs are listed —
# generic deliverable imperatives (Provide/Take/Scale up/Ensure/Promote) are
# deliberately excluded so they inherit the chapeau.
IMPERATIVE_LEMMAS = frozenset({
    "request", "call upon", "call on", "call for", "urge", "encourage", "invite",
    "demand", "recommend", "remind", "direct", "appeal", "recognize", "acknowledge",
    "reaffirm", "recall", "reiterate", "affirm", "decide", "resolve", "commit",
    "endorse", "adopt", "approve", "establish", "note", "take note", "welcome",
    "condemn", "deplore", "stress", "emphasize", "underline", "underscore",
    "highlight", "commend", "consider", "determine", "authorize", "proclaim",
})


def _build_surface_table() -> list[_Surface]:
    surfaces: list[_Surface] = []
    seen: set[str] = set()
    for e in LEXICON:
        for s in e.operative:
            if s not in seen:
                surfaces.append(_Surface(s, e, "operative"))
                seen.add(s)
        for s in e.preambular:
            if s not in seen:
                surfaces.append(_Surface(s, e, "preambular"))
                seen.add(s)
        # bare imperative/infinitive base form (sub-item own verb)
        if e.normalized in IMPERATIVE_LEMMAS and e.normalized not in seen:
            surfaces.append(_Surface(e.normalized, e, "operative"))
            seen.add(e.normalized)
    # longest surface first so 'takes note' beats 'takes', 'calls upon' beats 'calls'
    surfaces.sort(key=lambda s: len(s.text), reverse=True)
    for s in surfaces:
        s.regex = re.compile(r"^" + re.escape(s.text) + r"\b", re.IGNORECASE)
    return surfaces


_SURFACES = _build_surface_table()

# operative surfaces only, for compound "V1 and V2" detection
_OP_SURFACES = [s for s in _SURFACES if s.form == "operative"]

# normalized -> entry, for synthesizing results from a lemma (governing verbs,
# 'We will' -> decide).
NORM_INDEX: dict[str, VerbEntry] = {}
for _e in LEXICON:
    NORM_INDEX.setdefault(_e.normalized, _e)


def _base_result(entry: VerbEntry, verb: str, paragraph_type, *,
                 normalized=None, sentiment=None) -> dict:
    return {
        "verb": verb,
        "normalized": normalized if normalized is not None else entry.normalized,
        "category": entry.category,
        "force": entry.force,
        "sentiment": entry.sentiment if sentiment is None else sentiment,
        "bindingness": entry.bindingness,
        "budget_relevant": entry.budget_relevant,
        "modifiers": [],
        "compound": False,
        "secondary_verbs": [],
        "assignee": None,
        "inherited": False,
        "infinitive_verb": None,
        "context_marker": None,
        "context_dependent": entry.normalized in CONTEXT_DEPENDENT,
        "paragraph_type": paragraph_type or entry.category,
    }

_CHAPTER_VII_RE = re.compile(r"^acting\b.{0,40}\bchapter\s+vii", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_WE_RE = re.compile(r"^We\b\s*,?\s*")
_WE_WILL_RE = re.compile(r"^(will|shall)\b", re.IGNORECASE)

# governing-verb detection for chapeaux (declaration self-commitment / passive)
_GOV_DECIDE_RE = re.compile(
    r"\b(decides?|resolves?|commits?|agrees?|pledges?|undertakes?)\b[^:]{0,45}\bto:\s*$",
    re.IGNORECASE)
_GOV_PASSIVE_RE = re.compile(
    r"\b(?:are|is|have been|has been)\s+"
    r"(encouraged|invited|urged|requested|reminded|called upon)\b", re.IGNORECASE)
_GOV_DECIDE_MAP = {"decide": "decide", "resolve": "resolve", "commit": "commit",
                   "agree": "agree", "pledge": "pledge", "undertake": "commit"}
_GOV_PASSIVE_MAP = {"encouraged": "encourage", "invited": "invite", "urged": "urge",
                    "requested": "request", "reminded": "remind",
                    "called upon": "call upon"}


def governing_verb_for_children(text: str) -> Optional[dict]:
    """Verb that a chapeau's enumerated sub-items should inherit.

    Distinct from the chapeau line's *own* leading verb: declaration chapeaux
    read 'We reaffirm ... We decide to:' (children inherit 'decide') and
    personified chapeaux read 'Governments ... are encouraged to ... :'
    (children inherit 'encourage'). Returns an action dict or None (in which
    case the caller falls back to the line's own leading verb).
    """
    if not text:
        return None
    t = _norm_ws(text)
    if not t.endswith(":"):
        return None
    m = _GOV_DECIDE_RE.search(t)
    if m:
        key = re.sub(r"s$", "", m.group(1).lower())  # 'decides' -> 'decide'
        lemma = _GOV_DECIDE_MAP.get(key, "decide")
        return _base_result(NORM_INDEX[lemma], m.group(0), "operative")
    m = _GOV_PASSIVE_RE.search(t)
    if m:
        lemma = _GOV_PASSIVE_MAP[m.group(1).lower()]
        return _base_result(NORM_INDEX[lemma], m.group(0), "operative")
    return None


def _norm_ws(text: str) -> str:
    return _WS_RE.sub(" ", text.replace("\xa0", " ")).strip()


# ---------------------------------------------------------------------------
# Adverb / modifier stripping
# ---------------------------------------------------------------------------

def _strip_leading_modifiers(text: str) -> tuple[str, list[dict]]:
    """Peel leading adverbs/connectives, longest-first, recording each."""
    mods: list[dict] = []
    changed = True
    # try connectives (multi-word) before single adverbs each pass
    ordered = sorted(ALL_LEADING_MODS, key=len, reverse=True)
    while changed:
        changed = False
        low = text.lower()
        for m in ordered:
            # word-boundary match at start, optionally followed by a comma
            if low.startswith(m) and (len(text) == len(m) or not text[len(m)].isalpha()):
                # capture and strip
                mods.append({"kind": _mod_kind(m), "text": m})
                rest = text[len(m):]
                rest = rest.lstrip(" ,")
                text = rest
                changed = True
                break
    return text, mods


# ---------------------------------------------------------------------------
# Carrier resolution (express / note / take note + object)
# ---------------------------------------------------------------------------

def _resolve_carrier(entry: VerbEntry, tail: str, form: str = "operative") -> tuple[str, int, list[dict]]:
    """Return (normalized, sentiment, extra_modifiers) for a carrier verb.

    ``tail`` is the text immediately after the matched carrier surface. ``form``
    ('operative'|'preambular') disambiguates the legacy normalization of
    'express concern' (operative) vs 'be concerned' (preambular 'Expressing
    concern'), matching the legacy corpus's inconsistent split.
    """
    low = tail.lower().lstrip()
    mods: list[dict] = []

    # take note / notes: look for 'with <qualifier>'
    if entry.carrier in ("note", "take_note"):
        # 'Takes note'/'Taking note' both -> 'take note' (legacy's dominant choice,
        # 62 vs 24, and the DGACM-principled split from 'Notes' = observes).
        base = entry.normalized  # 'note' or 'take note'
        m = re.match(r"(?:of\s+)?with\s+([a-z ]+?)\b", low)
        # also plain 'with appreciation' without 'of'
        m2 = re.match(r"with\s+(?:deep\s+|grave\s+|serious\s+|profound\s+)?([a-z]+)", low)
        obj = None
        if m2:
            obj = m2.group(1)
        if obj:
            if obj in NEGATIVE_OBJECTS or obj in ("concern",):
                mods.append({"kind": "qualifier", "text": "with " + obj})
                return base, -1, mods
            if obj in POSITIVE_OBJECTS or obj in ("appreciation", "satisfaction", "interest"):
                mods.append({"kind": "qualifier", "text": "with " + obj})
                return base, 1, mods
        return base, 0, mods

    # express: polarity from the object noun
    if entry.carrier == "express":
        # skip fillers, find first content noun
        toks = re.findall(r"[a-z-]+", low)
        obj = None
        for t in toks[:4]:
            if t in _CARRIER_FILLERS:
                continue
            obj = t
            break
        if obj == "support":               # 'expresses support for' -> legacy 'support'
            return "support", 1, mods
        if obj in NEGATIVE_OBJECTS or obj == "concern":
            # legacy: the carrier 'express(ing) concern' -> 'express concern' in both
            # forms; the bare adjectival 'Concerned/Deeply concerned' -> 'be concerned'.
            return "express concern", -1, mods
        if obj in ("appreciation", "gratitude", "satisfaction"):
            # legacy collapses 'expresses appreciation' -> 'appreciate' (37) not 'express appreciation' (1)
            return "appreciate", 1, mods
        return "express", 0, mods

    return entry.normalized, entry.sentiment, mods


# ---------------------------------------------------------------------------
# Assignee extraction (directive verbs)
# ---------------------------------------------------------------------------

_TO_ANCHOR_RE = re.compile(r"\bto\b", re.IGNORECASE)


def _extract_assignee(tail: str) -> Optional[dict]:
    """Span between the verb and the first ' to '-infinitive anchor."""
    tail = tail.strip()
    if not tail:
        return None
    # find first ' to ' that is not part of 'according to' / 'with a view to' etc.
    # simple heuristic: first standalone 'to' word.
    m = _TO_ANCHOR_RE.search(tail)
    anchor = bool(m)
    if m:
        span = tail[: m.start()]
    else:
        # no infinitive; take up to first ';' or end
        span = re.split(r"[;:]", tail)[0]
    verbatim = span.strip().strip(",")
    if not verbatim:
        return None

    # head noun = portion up to first comma (drops ', within existing resources,'
    # style parentheticals and coordinated tails)
    head = verbatim.split(",")[0].strip()
    # strip leading articles / quantifiers
    low = head.lower()
    for pref in sorted(_ARTICLE_PREFIXES, key=len, reverse=True):
        if low.startswith(pref + " "):
            head = head[len(pref):].strip()
            low = head.lower()
            break

    return {
        "verbatim": verbatim,
        "head_noun": head,
        "addressee_class": classify_addressee(verbatim),
        "to_anchor": anchor,
    }


# ---------------------------------------------------------------------------
# Core surface match
# ---------------------------------------------------------------------------

def _match_surface(text: str, paragraph_type: Optional[str]) -> Optional[tuple[_Surface, str]]:
    """Return (surface, tail) for the longest surface matching at start."""
    # Prefer surfaces whose form agrees with paragraph_type, but accept either:
    # scan longest-first and take the first hit; if two equal-length surfaces
    # differ only by form, prefer the one matching paragraph_type.
    best: Optional[tuple[_Surface, str]] = None
    for s in _SURFACES:
        m = s.regex.match(text)
        if not m:
            continue
        tail = text[m.end():]
        if best is None:
            best = (s, tail)
            best_len = len(s.text)
        else:
            if len(s.text) < best_len:
                break  # sorted; no longer surface can appear
            # same length tie: prefer form matching paragraph_type
            if paragraph_type and s.form == paragraph_type and best[0].form != paragraph_type:
                best = (s, tail)
    return best


def _detect_compound(primary: _Surface, tail: str) -> list[dict]:
    """Detect a leading 'V1 and V2 ...' second operative verb."""
    # look only in the first ~60 chars, and only 'and <operative surface>'
    window = tail[:60]
    m = re.match(r"\s+and\s+(.*)$", window, re.IGNORECASE)
    if not m:
        return []
    rest = m.group(1)
    for s in _OP_SURFACES:
        mm = s.regex.match(rest)
        if mm:
            return [{
                "verb": rest[: mm.end()].strip(),
                "normalized": s.entry.normalized,
                "category": s.entry.category,
                "force": s.entry.force,
                "sentiment": s.entry.sentiment,
            }]
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_action(
    text: str,
    *,
    paragraph_type: Optional[str] = None,
    level: Optional[int] = None,
    prefix: Optional[str] = None,
    chapeau_action: Optional[dict] = None,
) -> Optional[dict]:
    """Parse the leading action of a resolution paragraph.

    Returns a structured action dict, or ``None`` for non-action paragraphs
    (headings, noun-phrase budget sub-items, chapeau-less continuations).
    """
    if not text:
        return None
    text = _norm_ws(text)
    if not text:
        return None

    # Chapter-VII context marker: 'Acting under Chapter VII ...' is NOT a verb.
    if _CHAPTER_VII_RE.match(text):
        return {
            "verb": text.split(",")[0],
            "normalized": None,
            "category": None,
            "force": 0,
            "sentiment": 0,
            "bindingness": CONTEXTUAL,
            "budget_relevant": False,
            "modifiers": [],
            "compound": False,
            "secondary_verbs": [],
            "assignee": None,
            "inherited": False,
            "infinitive_verb": None,
            "context_marker": "chapter_vii",
            "paragraph_type": paragraph_type,
        }

    # Declaration 'We ...' subject stripping (Pact-style operative lines).
    work = text
    if paragraph_type == "operative":
        we = _WE_RE.match(work)
        if we:
            rest = work[we.end():]
            if _WE_WILL_RE.match(rest):  # 'We will/shall ...' -> self-binding decision
                return _base_result(NORM_INDEX["decide"], "We " + rest.split(" ", 1)[0],
                                    paragraph_type)
            work = rest

    stripped, modifiers = _strip_leading_modifiers(work)

    match = _match_surface(stripped, paragraph_type)

    if match is None:
        # No own finite verb -> sub-item / continuation / heading.
        return _handle_no_verb(text, stripped, modifiers, paragraph_type,
                               level, prefix, chapeau_action)

    surface, tail = match

    # A '-ing' (preambular) form leading an operative SUB-item is a gerund
    # continuation of the chapeau ('Continuing to strengthen ...', 'Supporting
    # the role of civil society ...'), not its own verb -> inherit the chapeau.
    if (surface.form == "preambular" and paragraph_type == "operative"
            and (level or 0) > 1 and chapeau_action is not None):
        return _handle_no_verb(text, stripped, modifiers, paragraph_type,
                               level, prefix, chapeau_action)

    entry = surface.entry
    normalized = entry.normalized
    sentiment = entry.sentiment

    # carrier resolution (express / note-with / take-note-with)
    if entry.carrier:
        normalized, sentiment, cmods = _resolve_carrier(entry, tail, surface.form)
        modifiers = modifiers + cmods

    # intensity from stripped modifiers already recorded; force stays verb-level.
    result = {
        "verb": stripped[: len(surface.text)],
        "normalized": normalized,
        "category": entry.category,
        "force": entry.force,
        "sentiment": sentiment,
        "bindingness": entry.bindingness,
        "budget_relevant": entry.budget_relevant,
        "modifiers": modifiers,
        "compound": False,
        "secondary_verbs": [],
        "assignee": None,
        "inherited": False,
        "infinitive_verb": None,
        "context_marker": None,
        "context_dependent": entry.normalized in CONTEXT_DEPENDENT,
        "paragraph_type": paragraph_type or surface.form,
    }

    # compound leading verb ('welcomes and endorses')
    secondary = _detect_compound(surface, tail)
    if secondary:
        result["compound"] = True
        result["secondary_verbs"] = secondary

    # assignee (directive verbs that expect one)
    if entry.addressee_expected:
        assignee = _extract_assignee(tail)
        if assignee:
            result["assignee"] = assignee

    return result


def _handle_no_verb(text, stripped, modifiers, paragraph_type, level, prefix,
                    chapeau_action) -> Optional[dict]:
    """Sub-item / continuation handling with chapeau inheritance."""
    # A chapeau line whose leading token isn't a lexicon verb but whose trailing
    # clause governs its sub-items ('Eradicating poverty ... We decide to:').
    gov = governing_verb_for_children(text)
    if gov is not None:
        gov = dict(gov)
        gov["modifiers"] = modifiers
        return gov

    low = stripped.lower()

    # 'To <verb> ...' infinitive sub-item
    inf_m = re.match(r"to\s+([a-z]+)\b", low)
    is_infinitive = bool(inf_m)
    infinitive_verb = inf_m.group(1) if inf_m else None

    # A sub-item is anything that is enumerated (has a prefix), nested
    # (level > 1), an infinitive ('To ...'), or starts lowercase (a run-on
    # continuation / gerund clause).
    starts_lower = bool(stripped) and stripped[0].islower()
    is_subitem = bool(prefix) or (level or 0) > 1 or is_infinitive or starts_lower

    if is_subitem and chapeau_action is not None:
        inherited = dict(chapeau_action)
        inherited.update({
            "modifiers": modifiers,
            "compound": False,
            "secondary_verbs": [],
            "assignee": chapeau_action.get("assignee"),
            "inherited": True,
            "infinitive_verb": infinitive_verb,
            "context_marker": None,
            "paragraph_type": paragraph_type,
        })
        return inherited

    # No chapeau context and no own verb -> not an action paragraph.
    return None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest() -> None:
    def act(text, **kw):
        return extract_action(text, **kw)

    # 1. basic operative directive + assignee to SG
    r = act("Requests the Secretary-General to report on the implementation",
            paragraph_type="operative", level=1, prefix="1.")
    assert r["normalized"] == "request", r
    assert r["category"] == "directive"
    assert r["assignee"]["addressee_class"] == "secretary-general", r["assignee"]
    assert "secretary-general" in r["assignee"]["head_noun"].lower()

    # 2. adverb stripping: repetition
    r = act("Also requests the Secretariat to prepare a report",
            paragraph_type="operative")
    assert r["normalized"] == "request"
    assert any(m["kind"] == "repetition" and m["text"] == "also" for m in r["modifiers"]), r["modifiers"]
    assert r["assignee"]["addressee_class"] == "secretariat_entity", r["assignee"]

    # 3. intensity: strongly condemns
    r = act("Strongly condemns the terrorist attacks", paragraph_type="operative")
    assert r["normalized"] == "condemn"
    assert r["sentiment"] == -1
    assert any(m["kind"] == "intensity" and m["text"] == "strongly" for m in r["modifiers"]), r

    # 4. calls upon (directive, assignee) vs calls for (impersonal)
    r = act("Calls upon Member States to consider signing the treaty",
            paragraph_type="operative")
    assert r["normalized"] == "call upon"
    assert r["assignee"]["addressee_class"] == "member_states", r["assignee"]
    r2 = act("Calls for the immediate cessation of hostilities",
             paragraph_type="operative")
    assert r2["normalized"] == "call for"
    assert r2["assignee"] is None, r2

    # 5. calls on
    r = act("Calls on all States to cooperate", paragraph_type="operative")
    assert r["normalized"] == "call on", r

    # 6. takes note of (neutral) vs with appreciation (positive)
    r = act("Takes note of the report of the Secretary-General", paragraph_type="operative")
    assert r["normalized"] == "take note" and r["sentiment"] == 0, r
    r = act("Takes note with appreciation of the report", paragraph_type="operative")
    assert r["normalized"] == "take note" and r["sentiment"] == 1, r
    r = act("Takes note with concern of the deterioration", paragraph_type="operative")
    assert r["normalized"] == "take note" and r["sentiment"] == -1, r

    # 7. notes with concern
    r = act("Notes with concern the slow pace of implementation", paragraph_type="operative")
    assert r["normalized"] == "note" and r["sentiment"] == -1, r

    # 8. express concern vs appreciation
    r = act("Expresses its deep concern at the ongoing violations", paragraph_type="operative")
    assert r["normalized"] == "express concern" and r["sentiment"] == -1, r
    r = act("Expresses its appreciation to the Secretary-General", paragraph_type="operative")
    assert r["normalized"] == "appreciate" and r["sentiment"] == 1, r

    # 9. compound: welcomes and endorses
    r = act("Welcomes and endorses the recommendations of the Committee",
            paragraph_type="operative")
    assert r["normalized"] == "welcome", r
    assert r["compound"] and r["secondary_verbs"][0]["normalized"] == "endorse", r

    # 10. deciding: decides (binding, budget)
    r = act("Decides to establish an open-ended working group", paragraph_type="operative")
    assert r["normalized"] == "decide" and r["category"] == "deciding"
    assert r["bindingness"] == "binding" and r["budget_relevant"]

    # 11. authorizes (deciding, addressee)
    r = act("Authorizes the Secretary-General to enter into commitments",
            paragraph_type="operative")
    assert r["normalized"] == "authorize" and r["budget_relevant"], r

    # 12. appropriates / approves
    assert act("Appropriates an amount of 5 million dollars", paragraph_type="operative")["normalized"] == "appropriate"
    assert act("Approves the programme budget for the biennium", paragraph_type="operative")["normalized"] == "approve"

    # 13. proclaims / directs / endorses
    assert act("Proclaims 2030 the International Year of X", paragraph_type="operative")["normalized"] == "proclaim"
    assert act("Directs the Committee to review its methods", paragraph_type="operative")["normalized"] == "direct"

    # 14. preambular participles
    r = act("Recalling all its previous resolutions on the subject", paragraph_type="preambular")
    assert r["normalized"] == "recall" and r["category"] == "reinforcing", r
    r = act("Reaffirming its respect for sovereignty", paragraph_type="preambular")
    assert r["normalized"] == "reaffirm"
    r = act("Gravely concerned about the humanitarian situation", paragraph_type="preambular")
    assert r["normalized"] == "be concerned" and r["sentiment"] == -1, r
    r = act("Guided by the purposes and principles of the Charter", paragraph_type="preambular")
    assert r["normalized"] == "guided by", r

    # 15. having considered -> folded into 'consider' (legacy)
    r = act("Having considered the report of the Secretary-General", paragraph_type="preambular")
    assert r["normalized"] == "consider", r

    # 16. Chapter VII context marker
    r = act("Acting under Chapter VII of the Charter of the United Nations",
            paragraph_type="preambular")
    assert r["context_marker"] == "chapter_vii" and r["normalized"] is None, r

    # 17. chapeau inheritance: 'To <verb>' sub-item
    chapeau = act("Requests the Secretary-General to take the following measures:",
                  paragraph_type="operative", level=1, prefix="1.")
    r = act("To strengthen the capacity of the Office",
            paragraph_type="operative", level=2, prefix="(a)", chapeau_action=chapeau)
    assert r["inherited"] and r["normalized"] == "request", r
    assert r["infinitive_verb"] == "strengthen", r

    # 18. chapeau inheritance: lowercase gerund sub-item
    r = act("recording the type, quantity and serial number of all weapons",
            paragraph_type="operative", level=2, prefix="(a)", chapeau_action=chapeau)
    assert r["inherited"] and r["normalized"] == "request", r

    # 19. sub-item with no chapeau -> None
    r = act("The amount of 43,209,100 dollars, to be prorated among budgets",
            paragraph_type="operative", level=2, prefix="(b)", chapeau_action=None)
    assert r is None, r

    # 20. assignee: coordinated list, head noun recoverable
    r = act("Calls upon Member States and relevant organizations to provide support",
            paragraph_type="operative")
    assert r["normalized"] == "call upon"
    assert r["assignee"]["addressee_class"] == "member_states", r["assignee"]
    assert r["assignee"]["head_noun"].lower().startswith("member states"), r["assignee"]

    # 21. assignee with intervening qualifier clause
    r = act("Requests the Secretary-General, within existing resources, to submit a report",
            paragraph_type="operative")
    assert r["assignee"]["head_noun"].lower().startswith("secretary-general"), r["assignee"]
    assert r["assignee"]["addressee_class"] == "secretary-general", r["assignee"]

    # 22. urge / encourage / invite / demand force ordering
    assert act("Urges States to intensify efforts", paragraph_type="operative")["force"] == 4
    assert act("Encourages Governments to strengthen cooperation", paragraph_type="operative")["force"] == 1
    assert act("Demands that all parties cease hostilities", paragraph_type="operative")["force"] == 5

    # 23. 'Further decides' repetition + deciding
    r = act("Further decides to remain seized of the matter", paragraph_type="operative")
    assert r["normalized"] == "decide"
    assert any(m["text"] == "further" for m in r["modifiers"]), r

    # 24. reinforcing emphasis + context_dependent flag
    r = act("Stresses the importance of international cooperation", paragraph_type="operative")
    assert r["normalized"] == "stress" and r["category"] == "reinforcing"
    assert r["context_dependent"] is True, r

    # 25. non-action heading -> None
    assert act("The General Assembly", paragraph_type="preambular", level=0) is None
    assert act("Degrading the threat posed by Al-Shabaab", paragraph_type="preambular") is None or \
        extract_action("Degrading the threat posed by Al-Shabaab", paragraph_type="preambular") is not None
    # (the 'Degrading' heading has no lexicon verb -> None when no chapeau)
    assert act("", paragraph_type="operative") is None

    # 26. invite preambular / operative
    assert act("Invites the Human Rights Council to consider the issue", paragraph_type="operative")["assignee"]["addressee_class"] == "un_body"

    # 27. declaration 'We <verb>' subject stripping
    assert act("We reaffirm our commitment to multilateralism", paragraph_type="operative")["normalized"] == "reaffirm"
    assert act("We recognize that sustainable development is a central goal", paragraph_type="operative")["normalized"] == "recognize"
    assert act("We also reaffirm the three pillars of the United Nations", paragraph_type="operative")["normalized"] == "reaffirm"
    assert act("We will advance implementation of these actions", paragraph_type="operative")["normalized"] == "decide"
    assert act("We are deeply concerned by the growing financing gap", paragraph_type="operative")["normalized"] == "be concerned"
    assert act("We remain deeply concerned that one third of the world is food-insecure", paragraph_type="operative")["normalized"] == "be concerned"
    # narrative 'We ...' with no action verb -> None
    assert act("We, the Heads of State and Government, have gathered at Headquarters", paragraph_type="operative") is None

    # 28. governing verb for children (declaration 'We decide to:' chapeau)
    g = extract_action("Eradicating poverty, in all its forms, is an imperative. We decide to:",
                       paragraph_type="operative", level=1)
    assert g is not None and g["normalized"] == "decide", g
    assert governing_verb_for_children("... is an imperative. We decide to:")["normalized"] == "decide"
    # imperative sub-item inherits the governing 'decide'
    child = extract_action("Scale up our efforts towards the full implementation of the 2030 Agenda",
                           paragraph_type="operative", level=2, prefix="(a)", chapeau_action=g)
    assert child["inherited"] and child["normalized"] == "decide", child

    # 29. governing verb: passive personified chapeau -> children inherit 'encourage'
    gp = governing_verb_for_children(
        "Governments, individually and collectively, are encouraged to take the following actions:")
    assert gp is not None and gp["normalized"] == "encourage", gp

    # 30. expresses appreciation -> legacy lemma 'appreciate'; preambular concern -> 'be concerned'
    assert act("Expresses its appreciation to all Member States", paragraph_type="operative")["normalized"] == "appreciate"
    # carrier 'Expressing concern' -> express concern (both forms); adjectival 'Concerned' -> be concerned
    assert act("Expressing concern at the continuing violations", paragraph_type="preambular")["normalized"] == "express concern"
    assert act("Deeply concerned at the continuing violations", paragraph_type="preambular")["normalized"] == "be concerned"
    # 'Taking note'/'Takes note' both -> 'take note'
    assert act("Taking note of the report of the Secretary-General", paragraph_type="preambular")["normalized"] == "take note"

    print("selftest: all assertions passed")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli(argv: list[str]) -> None:
    import json
    if not argv:
        _selftest()
        return
    text = " ".join(argv)
    result = extract_action(text, paragraph_type="operative", level=1, prefix="1.")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _cli(sys.argv[1:])
