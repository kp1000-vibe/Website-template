"""Contact extraction.

The rule these pin down: a contact is only ever something the posting itself
published. Nothing is looked up, nothing is inferred from a company and a title.
Getting that wrong means cold emailing a stranger who never advertised a role.
"""

from jobagent.contacts import extract, summarise

HN_POST = """Acme Pay | Senior Product Manager, Platform | SF or Remote (US) | Full time

We are building the billing platform. Looking for someone with enterprise
integration experience, Salesforce and Oracle especially.

Email me at jane.mcleod@acmepay.com with a short note about something you shipped.
"""

X_POST = """My team is hiring a Staff PM for enterprise integrations.
DM me or reply here. We are remote friendly in the US.
"""

ATS_POST = """Acme Corp is an equal opportunity employer. Questions about this
posting may be directed to no-reply@acme.com or careers@acme.com.
You will report to Priya Raman, our VP of Product.
"""


def test_email_published_in_the_post_is_captured():
    contacts = extract(HN_POST)
    emails = [c for c in contacts if c.kind == "email"]
    assert len(emails) == 1
    assert emails[0].value == "jane.mcleod@acmepay.com"
    assert emails[0].confidence == "invited"      # the post says "email me"


def test_queue_addresses_are_ignored():
    """careers@ is the portal by another name, and the portal is what we skip."""
    values = [c.value for c in extract(ATS_POST)]
    assert "no-reply@acme.com" not in values
    assert "careers@acme.com" not in values
    for address in ["jobs@acme.com", "recruiting@acme.com", "hr@acme.com",
                    "talent@acme.com", "apply@acme.com"]:
        assert address not in [c.value for c in extract(f"Write to {address}.")]


def test_a_stated_manager_is_captured_with_its_wording():
    contacts = extract(ATS_POST)
    names = [c for c in contacts if c.kind == "name"]
    assert any(c.value == "Priya Raman" for c in names)
    stated = next(c for c in names if c.value == "Priya Raman")
    assert stated.confidence == "stated"
    assert "manager" in stated.why


def test_the_poster_is_the_contact_when_they_say_they_are_hiring():
    contacts = extract(X_POST, poster="@kpatro_builds", poster_platform="x")
    first = contacts[0]
    assert first.value == "@kpatro_builds"
    assert first.confidence == "invited"
    assert "they are hiring" in first.why


def test_a_poster_who_is_not_hiring_stays_weak():
    contacts = extract("Interesting thread about product roadmaps.",
                       poster="someuser", poster_platform="reddit")
    assert contacts[0].confidence == "weak"


def test_nothing_is_invented_from_an_empty_posting():
    assert extract("") == []
    assert extract("We are hiring. Apply through the portal.") == []
    assert summarise([]) == "no contact published in the posting"


def test_a_company_name_never_becomes_a_person():
    """Two capitalised words are not a human. Only explicit wording promotes one."""
    contacts = extract("Acme Pay is hiring a Senior Product Manager in San Francisco.")
    assert [c for c in contacts if c.kind == "name"] == []


def test_stated_contacts_rank_above_guesses():
    text = HN_POST + "\nFollow @acmepay for updates."
    contacts = extract(text)
    assert contacts[0].kind == "email"            # invited beats a mentioned handle
    assert contacts[-1].confidence == "weak"


def test_linkedin_profile_links_are_captured():
    contacts = extract("Questions? https://www.linkedin.com/in/jane-mcleod/ is me.")
    assert any(c.kind == "linkedin" and c.value.endswith("/in/jane-mcleod")
               for c in contacts)


def test_summary_line_names_the_best_contact():
    line = summarise(extract(HN_POST))
    assert line.startswith("jane.mcleod@acmepay.com")
    assert "published in the posting" in line


def test_an_address_outranks_a_username_at_the_same_confidence():
    """Both are invitations, but only one can be written to without a search."""
    contacts = extract(HN_POST, poster="janemcleod", poster_platform="hackernews")
    assert contacts[0].kind == "email"
    assert any(c.value == "janemcleod" for c in contacts)
