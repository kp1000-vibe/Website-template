"""Who to contact, taken only from what the posting itself publishes.

The useful thing is not guessing which director probably owns a req. It is
noticing that a person published "my team is hiring, email me" and capturing the
address they put there. That person wants to be contacted, the attribution is
certain, and nothing has to be inferred from anyone's profile.

So this module extracts contacts that appear IN the text of a posting. It never
looks a person up, and it never guesses a name from a company and a job title.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# Addresses that reach a queue rather than a person. Writing to careers@ is the
# same as applying through the portal, which is the thing this is meant to skip.
ROLE_ADDRESS = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|notifications?|support|info|hello|hi|"
    r"contact|admin|webmaster|privacy|legal|security|abuse|postmaster|"
    r"careers?|jobs?|hiring|recruit(ing|ment)?|talent|hr|people|apply|"
    r"resumes?|applications?|team|sales|billing|help|office)@", re.I
)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
X_HANDLE = re.compile(r"(?:^|[\s(<])@([A-Za-z0-9_]{2,15})\b")
LINKEDIN = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?")

# "email me at", "reach out to Jane", "DM me", "hiring manager is Jane Doe"
_NAME = r"([A-Z][a-z]+(?:\s+[A-Z][a-z'\-]+){0,2})"
NAMED_CONTACT = [
    (re.compile(rf"hiring manager\s*(?:is|:)\s*{_NAME}"), "stated hiring manager"),
    (re.compile(rf"reach(?:ing)? out to\s+{_NAME}"), "invited contact"),
    (re.compile(rf"(?:email|contact|message|ping|ask)\s+{_NAME}\s+(?:at|on|via)\b"), "invited contact"),
    (re.compile(rf"{_NAME}\s*,?\s*(?:our|the)\s+(?:hiring manager|recruiter|talent partner)"),
     "stated hiring manager"),
    (re.compile(rf"report(?:ing|s)? (?:directly )?to\s+{_NAME}"), "stated manager"),
    (re.compile(rf"(?:posted|shared) by\s+{_NAME}"), "poster"),
]
# First person hiring language is what makes a post worth a direct reply.
FIRST_PERSON_HIRING = re.compile(
    r"\b(?:my|our)\s+team\s+is\s+hiring|\bi'?m\s+hiring\b|\bwe(?:'re| are)\s+hiring\b"
    r"|\bi\s+am\s+looking\s+for\b|\bdm\s+me\b|\bemail\s+me\b|\breach\s+out\s+to\s+me\b",
    re.I,
)


@dataclass
class Contact:
    kind: str          # email | x | linkedin | name
    value: str
    why: str           # how we came by it, shown to you verbatim
    confidence: str    # stated | invited | weak

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_email(raw: str) -> str:
    return raw.strip().strip(".,;:)>]")


def extract(text: str, *, poster: str | None = None,
            poster_platform: str | None = None) -> list[Contact]:
    """Pull contacts out of one posting. Order is best first."""
    if not text:
        text = ""
    found: list[Contact] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str, why: str, confidence: str) -> None:
        key = (kind, value.lower())
        if value and key not in seen:
            seen.add(key)
            found.append(Contact(kind, value, why, confidence))

    invites_contact = bool(FIRST_PERSON_HIRING.search(text))

    # The person who wrote the post, when the source knows who that was.
    if poster:
        add("x" if poster_platform == "x" else "name", poster,
            f"posted on {poster_platform or 'the source'}"
            + (", and the post says they are hiring" if invites_contact else ""),
            "invited" if invites_contact else "weak")

    for raw in EMAIL.findall(text):
        address = _clean_email(raw)
        if ROLE_ADDRESS.match(address):
            continue
        add("email", address, "published in the posting",
            "invited" if invites_contact else "stated")

    for pattern, why in NAMED_CONTACT:
        for match in pattern.findall(text):
            name = match.strip() if isinstance(match, str) else match[0].strip()
            # Two capitalised words that are actually a company or a product slip
            # through regexes like this, so weak confidence unless stated outright.
            add("name", name, why,
                "stated" if why.startswith("stated") else "invited")

    for url in LINKEDIN.findall(text):
        add("linkedin", url.rstrip("/"), "linked in the posting", "stated")

    for handle in X_HANDLE.findall(text):
        add("x", "@" + handle, "mentioned in the posting", "weak")

    # Confidence first, then how directly you can actually reach the person. An
    # address you can write to beats a username you would have to go and find.
    confidence_rank = {"stated": 0, "invited": 1, "weak": 2}
    kind_rank = {"email": 0, "linkedin": 1, "x": 2, "name": 3}
    found.sort(key=lambda c: (confidence_rank.get(c.confidence, 3),
                              kind_rank.get(c.kind, 4)))
    return found


def summarise(contacts: list[Contact]) -> str:
    """One line for the dashboard and the brief."""
    if not contacts:
        return "no contact published in the posting"
    best = contacts[0]
    extra = f" (+{len(contacts) - 1} more)" if len(contacts) > 1 else ""
    return f"{best.value} — {best.why}{extra}"
