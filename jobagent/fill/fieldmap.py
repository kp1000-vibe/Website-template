"""Label to answer rules.

A rule is (kind, patterns, value). We build the rule list once from your profile,
then every form field on every ATS gets matched against it in order. Order
matters: the first match wins, so put the specific rules above the generic ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from ..config import Profile

TEXT = "text"
CHOICE = "choice"     # select or radio group, value is the option we want
FILE = "file"
LONGTEXT = "longtext"


@dataclass
class Rule:
    key: str
    kind: str
    patterns: list[re.Pattern]
    value: str | Callable[[], str]
    # Some fields we deliberately refuse to touch even when we could.
    skip: bool = False
    note: str = ""

    def matches(self, label: str) -> bool:
        return any(p.search(label) for p in self.patterns)

    def resolve(self) -> str:
        return self.value() if callable(self.value) else str(self.value or "")


def _p(*patterns: str) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


def _yesno(flag) -> str:
    if isinstance(flag, str):
        return flag
    return "Yes" if flag else "No"


def build_rules(profile: Profile, resume_path: str) -> list[Rule]:
    ident = profile.identity
    elig = profile.eligibility
    prefs = profile.preferences
    eeo = profile.eeo
    answers = profile.answers

    full_name = ident.get("full_name", "")
    first = ident.get("first_name") or full_name.split(" ")[0] if full_name else ""
    last = ident.get("last_name") or (full_name.split(" ")[-1] if " " in full_name else "")

    rules: list[Rule] = [
        # --- name, in the specific-before-generic order that matters ---
        Rule("first_name", TEXT, _p(r"\bfirst\s*name\b", r"\bgiven name\b", r"^fname$"), first),
        Rule("last_name", TEXT, _p(r"\blast\s*name\b", r"\bsurname\b", r"\bfamily name\b", r"^lname$"), last),
        Rule("preferred_name", TEXT, _p(r"preferred (first )?name", r"\bnickname\b"),
             ident.get("preferred_name") or first),
        Rule("full_name", TEXT, _p(r"\bfull\s*name\b", r"^name$", r"\byour name\b"), full_name),

        # --- contact ---
        Rule("email", TEXT, _p(r"\be-?mail\b"), ident.get("email", "")),
        Rule("phone", TEXT, _p(r"\bphone\b", r"\bmobile\b", r"\btelephone\b", r"\bcell\b"),
             ident.get("phone", "")),

        # --- links, linkedin before the generic website rule ---
        Rule("linkedin", TEXT, _p(r"linked\s*in"), ident.get("linkedin", "")),
        Rule("github", TEXT, _p(r"\bgithub\b"), ident.get("github", "")),
        Rule("portfolio", TEXT, _p(r"\bportfolio\b", r"personal (web)?site", r"\bwebsite\b",
                                   r"\bblog\b", r"\burl\b"), ident.get("website", "")),
        Rule("twitter", TEXT, _p(r"\btwitter\b", r"\bx\.com\b"), ident.get("twitter", "")),

        # --- location ---
        Rule("current_city", TEXT, _p(r"\bcity\b", r"current location", r"where are you (based|located)",
                                      r"\blocation\b"), ident.get("city", "")),
        Rule("state", TEXT, _p(r"\bstate\b", r"\bprovince\b", r"\bregion\b"), ident.get("state", "")),
        Rule("postal", TEXT, _p(r"\bzip\b", r"postal code"), ident.get("postal_code", "")),
        Rule("country", CHOICE, _p(r"\bcountry\b"), ident.get("country", "United States")),
        Rule("address", TEXT, _p(r"street address", r"address line 1", r"^address$"),
             ident.get("street_address", "")),

        # --- eligibility, the questions that sink applications when answered wrong ---
        Rule("work_auth", CHOICE,
             _p(r"legally (authoriz|entitl)ed to work", r"authorized to work",
                r"eligible to work", r"right to work", r"work authorization"),
             _yesno(elig.get("authorized_to_work", True))),
        Rule("sponsorship", CHOICE,
             _p(r"require .*sponsor", r"need .*sponsor", r"sponsorship (now|in the future)",
                r"visa sponsorship", r"will you .*require .*visa"),
             _yesno(elig.get("requires_sponsorship", False))),
        Rule("clearance", CHOICE, _p(r"security clearance"),
             _yesno(elig.get("security_clearance", False))),
        Rule("age18", CHOICE, _p(r"(at least|over) 18", r"18 years"), "Yes"),
        Rule("previously_employed", CHOICE,
             _p(r"(previously|ever) (been )?(employed|worked) (at|for|with|by)",
                r"former employee"),
             _yesno(elig.get("previously_employed_here", False))),
        Rule("non_compete", CHOICE, _p(r"non-?compete", r"restrictive covenant"),
             _yesno(elig.get("non_compete", False))),

        # --- logistics ---
        Rule("start_date", TEXT, _p(r"start date", r"available to start", r"when can you start",
                                    r"notice period", r"earliest availability"),
             prefs.get("start_date", "")),
        Rule("salary", TEXT, _p(r"salary (expectation|requirement)", r"desired (salary|compensation)",
                                r"compensation expectation", r"expected (salary|compensation)",
                                r"base salary"),
             str(prefs.get("salary_answer", ""))),
        Rule("relocate", CHOICE, _p(r"willing to relocate", r"open to relocation"),
             _yesno(prefs.get("relocation", False))),
        Rule("onsite", CHOICE, _p(r"willing to work (on-?site|in office|hybrid)",
                                  r"comfortable with .*(hybrid|on-?site)",
                                  r"able to commute"),
             _yesno(prefs.get("onsite_ok", True))),
        Rule("hear_about", CHOICE, _p(r"how did you (hear|find out)", r"source", r"referred by"),
             answers.get("how_did_you_hear", "Company website")),
        Rule("pronouns", TEXT, _p(r"\bpronouns?\b"), ident.get("pronouns", "")),

        # --- documents ---
        Rule("resume", FILE, _p(r"\bresume\b", r"\bcv\b", r"upload.*(resume|cv)", r"attach.*(resume|cv)"),
             resume_path),
        Rule("cover_letter", LONGTEXT, _p(r"cover letter"), ""),  # filled from generated material

        # --- eeo, answered exactly as you told us and never guessed ---
        Rule("gender", CHOICE, _p(r"\bgender\b", r"\bsex\b"), eeo.get("gender", "Decline to self identify")),
        Rule("race", CHOICE, _p(r"\brace\b", r"\bethnicity\b", r"hispanic or latino"),
             eeo.get("race", "Decline to self identify")),
        Rule("veteran", CHOICE, _p(r"\bveteran\b", r"protected veteran", r"military service"),
             eeo.get("veteran_status", "I do not wish to answer")),
        Rule("disability", CHOICE, _p(r"\bdisabilit(y|ies)\b", r"\bdisabled\b"),
             eeo.get("disability_status", "I do not wish to answer")),

        # --- things we will not answer for you ---
        Rule("consent", CHOICE, _p(r"\bconsent\b", r"\bagree to\b", r"terms and conditions",
                                   r"privacy (policy|notice)", r"subscribe", r"marketing"),
             "", skip=True, note="consent and marketing boxes are yours to tick"),
        Rule("password", TEXT, _p(r"password", r"passcode"), "", skip=True,
             note="account credentials are never filled by the agent"),
        Rule("ssn", TEXT, _p(r"social security", r"\bssn\b", r"national id", r"date of birth",
                             r"\bdob\b"),
             "", skip=True, note="identity numbers are never filled by the agent"),
    ]
    return rules


def match(rules: list[Rule], label: str) -> Rule | None:
    for rule in rules:
        if rule.matches(label):
            return rule
    return None
