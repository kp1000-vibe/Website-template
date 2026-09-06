"""Form field matching: the difference between a filled form and a wrong answer."""

from jobagent.config import Profile
from jobagent.fill import fieldmap
from jobagent.fill.filler import _best_option, _question_answer

PROFILE = Profile(raw={
    "identity": {"full_name": "Alex Rivera", "email": "a@example.com",
                 "phone": "555-0100", "linkedin": "https://linkedin.com/in/alex",
                 "website": "https://alex.dev", "city": "Austin", "state": "TX"},
    "eligibility": {"authorized_to_work": True, "requires_sponsorship": False},
    "preferences": {"relocation": False, "salary_answer": "180000"},
    "eeo": {"gender": "Decline to self identify"},
    "standard_answers": {"how_did_you_hear": "Company website"},
})
RULES = fieldmap.build_rules(PROFILE, "/tmp/resume.pdf")


def matched(label):
    rule = fieldmap.match(RULES, label)
    return (rule.key, rule.resolve()) if rule else (None, None)


def test_name_fields_do_not_collide():
    assert matched("First Name *")[0] == "first_name"
    assert matched("Last Name *")[0] == "last_name"
    assert matched("Full name")[0] == "full_name"


def test_the_two_questions_that_matter():
    key, value = matched("Are you legally authorized to work in the United States?")
    assert (key, value) == ("work_auth", "Yes")
    key, value = matched("Will you now or in the future require sponsorship for employment visa status?")
    assert (key, value) == ("sponsorship", "No")


def test_links_are_not_confused_with_each_other():
    assert matched("LinkedIn Profile")[0] == "linkedin"
    assert matched("Website")[0] == "portfolio"
    assert matched("GitHub URL")[0] == "github"


def test_dangerous_fields_are_skipped():
    for label in ["Password", "Social Security Number", "Date of Birth",
                  "I agree to the terms and conditions",
                  "Subscribe me to marketing emails"]:
        rule = fieldmap.match(RULES, label)
        assert rule is not None and rule.skip, label


def test_select_option_matching():
    yes_no = [{"value": "1", "text": "Yes"}, {"value": "0", "text": "No"}]
    assert _best_option(yes_no, "Yes") == "Yes"
    assert _best_option(yes_no, "No") == "No"

    eeo = [{"value": "a", "text": "Male"}, {"value": "b", "text": "Female"},
           {"value": "c", "text": "Decline to self identify"}]
    assert _best_option(eeo, "Decline to self identify") == "Decline to self identify"

    # "No" must not match "Not Hispanic or Latino" ahead of a real no
    tricky = [{"value": "a", "text": "Not Hispanic or Latino"},
              {"value": "b", "text": "No"}]
    assert _best_option(tricky, "No") == "No"
    assert _best_option([], "Yes") is None


def test_question_answer_fuzzy_match():
    answers = [
        {"question": "Why do you want to work at this company?", "answer": "A"},
        {"question": "Describe a product you shipped and the outcome it drove.", "answer": "B"},
    ]
    assert _question_answer("Why do you want to work here?", answers) == "A"
    assert _question_answer("Tell us about a product you shipped", answers) == "B"
    assert _question_answer("What is your favourite colour?", answers) is None
