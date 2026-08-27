"""Evaluate the deterministic action-verb parser against the legacy LLM corpus.

Runs ``fulltext_verbs.extract_action`` over ``digitallibrary.document_paragraphs``
(operative + preambular rows, with chapeau context resolved from the element
sequence) for the documents that also exist in the legacy corpus, then joins the
legacy ground truth (``mandates.paragraphs`` -> ``mandates.paragraph_mandates``
[+ ``mandate_assignees``]) by TEXT similarity (positions differ between corpora)
and reports:

  (a) coverage: % of operative/preambular rows with an extracted verb;
  (b) agreement with legacy on normalized verb (exact + lemma) and on category;
  (c) assignee head-noun agreement on directive rows;
  (d) top-30 disagreement patterns with counts and examples;
  (e) the unmatched-verb tail (our None rows) sampled and categorized.

Read-only. The legacy ``mandates`` schema is not visible to the worktree's
``digitallibrary_rw`` role, so this script prefers a DATABASE_URL that can read
both schemas: it tries the worktree .env first and, if the mandates schema is
denied, falls back to the sibling mandates-repo .env (same Postgres database).

Usage:
    python fulltext_verbs_eval.py           # full report
    python fulltext_verbs_eval.py --round 1 # label the round
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter, defaultdict

import psycopg
from dotenv import dotenv_values

import fulltext_verbs as fv

WORKTREE_ENV = "/Users/david/UN/digitallibrary.unfck.org/.claude/worktrees/fulltexts/.env"
MANDATES_ENV = "/Users/david/UN/mandates/.env"
SSL_CERTS = ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"]


def _with_ssl(url: str) -> str:
    if "sslrootcert" in url:
        return url
    for p in SSL_CERTS:
        if os.path.exists(p):
            return url + ("&" if "?" in url else "?") + "sslrootcert=" + p
    return url


def get_conn() -> psycopg.Connection:
    """Open a connection that can read BOTH digitallibrary and mandates schemas."""
    candidates = []
    for path in (WORKTREE_ENV, MANDATES_ENV):
        vals = dotenv_values(path)
        u = vals.get("DATABASE_URL")
        if u:
            candidates.append(u.replace(":6432/", ":5432/"))  # direct port, no pooler
    for url in candidates:
        try:
            conn = psycopg.connect(_with_ssl(url))
            cur = conn.cursor()
            cur.execute("select 1 from mandates.paragraph_mandates limit 1")
            cur.fetchone()
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            continue
    raise RuntimeError("No DATABASE_URL can read both digitallibrary and mandates schemas")


# ---------------------------------------------------------------------------
# Text normalization for cross-corpus matching
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_LEAD_PREFIX = re.compile(r"^\s*(\(?[a-z0-9]{1,4}\)|[0-9]{1,3}\.)\s+", re.IGNORECASE)


def match_key(text: str) -> str:
    """Normalize a paragraph's text to a comparison key (first 60 chars)."""
    t = text.replace("\xa0", " ").lower()
    t = _LEAD_PREFIX.sub("", t)              # drop any leading enumerator
    t = _WS.sub(" ", t).strip()
    t = re.sub(r"[^a-z0-9 ]", "", t)         # drop punctuation/quotes
    return t[:60]


def norm_symbol(s: str) -> str:
    return s.upper().replace(" ", "").strip()


def lemma_of(normalized: str | None) -> str | None:
    if not normalized:
        return None
    # collapse call upon/on/for -> call ; express concern/appreciation -> express
    return normalized.split()[0]


# ---------------------------------------------------------------------------
# Load legacy ground truth
# ---------------------------------------------------------------------------

def load_legacy(conn):
    """Return {norm_symbol: {match_key: legacy_record}}."""
    cur = conn.cursor()
    cur.execute("""
        select p.document_symbol, p.position, p.paragraph_type, p.text,
               pm.mandate_index, pm.action_verb_normalized, pm.action_verb_type,
               pm.id as mandate_id
        from mandates.paragraphs p
        join mandates.paragraph_mandates pm on pm.paragraph_id = p.id
        order by p.document_symbol, p.position, pm.mandate_index
    """)
    rows = cur.fetchall()

    # assignees for primary mandate (index 0)
    cur.execute("select mandate_id, assignee_normalized, assignee_type from mandates.mandate_assignees")
    assignees = defaultdict(list)
    for mid, an, at in cur.fetchall():
        assignees[mid].append((an, at))

    legacy = defaultdict(dict)
    for sym, pos, ptype, text, midx, verb_n, verb_t, mandate_id in rows:
        if midx != 0:
            continue  # compare against the primary mandate of the paragraph
        key = match_key(text or "")
        if not key:
            continue
        assg = assignees.get(mandate_id, [])
        legacy[norm_symbol(sym)][key] = {
            "text": text,
            "paragraph_type": ptype,
            "verb": verb_n,
            "type": verb_t,
            "assignees": assg,
        }
    return legacy


# ---------------------------------------------------------------------------
# Run parser over DL rows with chapeau resolution
# ---------------------------------------------------------------------------

def run_parser_over_doc(conn, symbol_normalized):
    """Yield (dl_row, action) for operative+preambular rows in position order."""
    cur = conn.cursor()
    cur.execute("""
        select position, paragraph_type, level, prefix, lead_verb, text
        from digitallibrary.document_paragraphs
        where symbol_normalized = %s and paragraph_type in ('operative','preambular')
        order by position
    """, [symbol_normalized])
    chapeau = None
    out = []
    for position, ptype, level, prefix, lead_verb, text in cur.fetchall():
        stripped = (text or "").rstrip()
        is_colon = stripped.endswith(":")
        lvl = level if level is not None else (1 if ptype == "operative" else 0)
        # a new top-level, non-chapeau operative resets inherited context
        if lvl <= 1 and not is_colon:
            chapeau = None
        action = fv.extract_action(
            text, paragraph_type=ptype, level=level, prefix=prefix,
            chapeau_action=chapeau,
        )
        # this row becomes the chapeau for following sub-items if it opens a list.
        # A governing verb (declaration 'We decide to:' / passive 'are encouraged
        # ... :') overrides the line's own leading verb for what children inherit.
        gov = fv.governing_verb_for_children(text)
        if gov is not None:
            chapeau = gov
        elif (action and not action.get("inherited") and is_colon
                and action.get("normalized")):
            chapeau = action
        out.append({
            "position": position, "paragraph_type": ptype, "level": level,
            "prefix": prefix, "text": text, "action": action,
        })
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(round_label=None):
    conn = get_conn()
    legacy = load_legacy(conn)

    # overlap docs
    cur = conn.cursor()
    cur.execute("select distinct symbol_normalized from digitallibrary.document_paragraphs")
    dl_syms = {r[0] for r in cur.fetchall()}
    overlap = sorted(s for s in legacy if s in dl_syms)

    # counters
    n_oper = n_oper_verb = 0
    n_pre = n_pre_verb = 0
    n_matched = 0                    # DL rows matched to a legacy paragraph
    n_match_candidates = 0           # DL rows with own verb eligible to match
    verb_exact = verb_lemma = cat_agree = 0
    n_compare = 0                    # matched rows where both have a verb
    assignee_total = assignee_agree = 0
    disagree = Counter()
    disagree_examples = defaultdict(list)
    cat_disagree = Counter()
    cat_disagree_examples = defaultdict(list)
    none_tail = []                   # our None rows (operative) for tail analysis

    for sym in overlap:
        legacy_paras = legacy[sym]
        used_keys = set()
        for row in run_parser_over_doc(conn, sym):
            ptype = row["paragraph_type"]
            action = row["action"]
            has_verb = bool(action and action.get("normalized"))
            if ptype == "operative":
                n_oper += 1
                if has_verb:
                    n_oper_verb += 1
                elif (row["level"] or 1) <= 1 and not (row["prefix"]):
                    # a top-level operative with no verb and no prefix -> tail case
                    none_tail.append(row["text"])
            else:
                n_pre += 1
                if has_verb:
                    n_pre_verb += 1

            # try to match to legacy for agreement metrics
            key = match_key(row["text"] or "")
            lg = legacy_paras.get(key)
            if lg is None and key:
                # exact-prefix fallback: legacy key is a prefix of ours or vice versa
                for lk, lv in legacy_paras.items():
                    if lk in used_keys:
                        continue
                    if lk.startswith(key[:40]) or key.startswith(lk[:40]):
                        lg = lv
                        key = lk
                        break
            if has_verb:
                n_match_candidates += 1
            if lg is not None:
                used_keys.add(key)
                if has_verb:
                    n_matched += 1
                    our_v = action["normalized"]
                    their_v = lg["verb"]
                    if their_v:
                        n_compare += 1
                        if our_v == their_v:
                            verb_exact += 1
                        if lemma_of(our_v) == lemma_of(their_v):
                            verb_lemma += 1
                        else:
                            pat = f"{their_v!r:>22} -> {our_v!r}"
                            disagree[pat] += 1
                            if len(disagree_examples[pat]) < 2:
                                disagree_examples[pat].append(row["text"][:150])
                        # category
                        our_c = action["category"]
                        their_c = lg["type"]
                        if their_c:
                            if our_c == their_c:
                                cat_agree += 1
                            else:
                                cpat = f"{their_c} -> {our_c} (verb {their_v}/{our_v})"
                                cat_disagree[cpat] += 1
                                if len(cat_disagree_examples[cpat]) < 2:
                                    cat_disagree_examples[cpat].append(row["text"][:130])
                    # assignee agreement (directive rows with an assignee both sides)
                    if action["category"] == "directive" and action.get("assignee") and lg["assignees"]:
                        assignee_total += 1
                        our_head = (action["assignee"]["head_noun"] or "").lower()
                        their = " ".join((a[0] or "").lower() for a in lg["assignees"])
                        if _assignee_match(our_head, their):
                            assignee_agree += 1

    # -------- report --------
    L = f" (round {round_label})" if round_label else ""
    print("=" * 72)
    print(f"ACTION-VERB PARSER EVALUATION{L}")
    print("=" * 72)
    print(f"overlap documents (legacy ∩ digitallibrary): {len(overlap)}")
    print()
    print("(a) COVERAGE (own extracted verb, incl. chapeau-inherited)")
    print(f"    operative : {n_oper_verb:5}/{n_oper:<5} = {pct(n_oper_verb, n_oper)}")
    print(f"    preambular: {n_pre_verb:5}/{n_pre:<5} = {pct(n_pre_verb, n_pre)}")
    print()
    print("(match) DL rows matched to a legacy paragraph by text:")
    print(f"    matched {n_matched} rows; comparable (both have verb): {n_compare}")
    print(f"    match rate over verb-bearing DL rows: {pct(n_matched, n_match_candidates)}")
    print()
    print("(b) AGREEMENT WITH LEGACY (over comparable matched rows)")
    print(f"    normalized verb, exact : {verb_exact}/{n_compare} = {pct(verb_exact, n_compare)}")
    print(f"    normalized verb, lemma : {verb_lemma}/{n_compare} = {pct(verb_lemma, n_compare)}")
    print(f"    category               : {cat_agree}/{n_compare} = {pct(cat_agree, n_compare)}")
    print()
    print("(c) ASSIGNEE head-noun agreement (directive rows, both sides present)")
    print(f"    {assignee_agree}/{assignee_total} = {pct(assignee_agree, assignee_total)}")
    print()
    print("(d) TOP DISAGREEMENT PATTERNS  (legacy_verb -> our_verb : count)")
    for pat, c in disagree.most_common(30):
        print(f"    {c:4}  {pat}")
        for ex in disagree_examples[pat]:
            print(f"            e.g. {ex}")
    print()
    print("    CATEGORY disagreements (legacy -> ours : count)")
    for pat, c in cat_disagree.most_common(15):
        print(f"    {c:4}  {pat}")
        for ex in cat_disagree_examples[pat]:
            print(f"            e.g. {ex}")
    print()
    print(f"(e) OUR-NONE TAIL (top-level operative, no verb, no prefix): {len(none_tail)}")
    for t in none_tail[:25]:
        print(f"    - {t[:130]}")
    print("=" * 72)
    conn.close()
    return {
        "overlap": len(overlap), "n_oper": n_oper, "n_oper_verb": n_oper_verb,
        "n_pre": n_pre, "n_pre_verb": n_pre_verb, "n_compare": n_compare,
        "verb_exact": verb_exact, "verb_lemma": verb_lemma, "cat_agree": cat_agree,
    }


def _assignee_match(our_head: str, their: str) -> bool:
    """Loose head-noun agreement between our span and legacy assignee(s)."""
    if not our_head or not their:
        return False
    # direct substring either direction
    if our_head in their or their in our_head:
        return True
    # shared salient token
    STOP = {"the", "of", "and", "all", "united", "nations", "states", "its", "a"}
    ours = {w for w in re.findall(r"[a-z-]+", our_head) if w not in STOP and len(w) > 3}
    theirs = {w for w in re.findall(r"[a-z-]+", their) if w not in STOP and len(w) > 3}
    if ours & theirs:
        return True
    # class-level synonyms
    syn = [
        ("secretary-general", "secretary-general"),
        ("member state", "member states"),
        ("state", "member states"),
    ]
    for a, b in syn:
        if a in our_head and b in their:
            return True
    return False


def pct(n, d):
    return f"{100.0 * n / d:5.1f}%" if d else "  n/a"


if __name__ == "__main__":
    rl = None
    if "--round" in sys.argv:
        i = sys.argv.index("--round")
        rl = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    evaluate(rl)
