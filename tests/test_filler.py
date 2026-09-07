"""The filler, driven against a fake page.

No browser here. The fake stands in for Playwright and records what would have
been typed, which is the thing that actually matters: the right answer in the
right box, and nothing at all in the boxes we refuse to touch.
"""

import json

import pytest

from jobagent.config import Profile
from jobagent.fill.filler import Filler

PROFILE = Profile(raw={
    "identity": {"full_name": "Alex Rivera", "email": "alex@example.com",
                 "phone": "555-0100", "linkedin": "https://linkedin.com/in/alex",
                 "city": "Austin", "country": "United States"},
    "eligibility": {"authorized_to_work": True, "requires_sponsorship": False},
    "preferences": {"salary_answer": "180000", "start_date": "Two weeks from offer"},
    "eeo": {"gender": "Decline to self identify", "veteran_status": "I do not wish to answer"},
    "standard_answers": {"how_did_you_hear": "Company website"},
})


class _Zero:
    """Stands in for a locator that matches nothing."""

    def count(self):
        return 0

    def click(self, timeout=None):
        raise AssertionError("the filler must never click anything on the page")


class FakeLocator:
    def __init__(self, page, idx):
        self.page, self.idx = page, idx

    def _spec(self):
        return self.page.by_idx[self.idx]

    def fill(self, value, timeout=None):
        self.page.actions.append(("fill", self._spec()["label"], value))
        self._spec()["value"] = value

    def select_option(self, label=None, timeout=None):
        self.page.actions.append(("select", self._spec()["label"], label))
        self._spec()["value"] = label

    def check(self, timeout=None):
        self.page.actions.append(("check", self._spec()["label"], True))
        self._spec()["checked"] = True

    def set_input_files(self, path, timeout=None):
        self.page.actions.append(("upload", self._spec()["label"], path))
        self._spec()["value"] = path

    def count(self):
        return 1


class FakePage:
    """Enough of the Playwright page surface for the filler to run."""

    def __init__(self, fields, radio_options=None):
        self.fields = fields
        self.by_idx = {str(f["idx"]): f for f in fields}
        self.radio_options = radio_options or {}
        self.actions = []

    def evaluate(self, js, arg=None):
        if arg is not None:                 # the radio group lookup
            return self.radio_options.get(arg, [])
        return [dict(f) for f in self.fields]

    def locator(self, selector):
        if "data-jobagent-idx" not in selector:
            return _Zero()          # the "is the form already open" probe
        return FakeLocator(self, selector.split('"')[1])

    def wait_for_timeout(self, ms):
        pass

    def get_by_role(self, role, name=None):
        return _Zero()


def field(idx, label, type="text", tag="input", required=False, options=None, name=""):
    spec = {"idx": idx, "type": type, "tag": tag, "name": name, "label": label,
            "required": required, "value": "", "checked": False}
    if options is not None:
        spec["options"] = options
    return spec


@pytest.fixture
def job_row():
    return {"id": "abc123", "url": "https://example.com/apply", "ats": "greenhouse"}


@pytest.fixture
def artifacts(tmp_path):
    answers = [{"question": "Why do you want to work at this company?",
                "answer": "Because of the billing platform work."}]
    (tmp_path / "answers.json").write_text(json.dumps(answers))
    (tmp_path / "cover_letter.txt").write_text("Short letter body.")
    return {"answers": str(tmp_path / "answers.json"),
            "cover_letter": str(tmp_path / "cover_letter.txt")}


def run(fields, job_row, artifacts, tmp_path, radio_options=None):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    page = FakePage(fields, radio_options)
    report = Filler(PROFILE, resume).fill_page(page, job_row, artifacts)
    return page, report


def test_fills_a_standard_greenhouse_form(job_row, artifacts, tmp_path):
    fields = [
        field(0, "First Name *", required=True),
        field(1, "Last Name *", required=True),
        field(2, "Email *", type="email", required=True),
        field(3, "Phone"),
        field(4, "Resume/CV *", type="file", required=True),
        field(5, "LinkedIn Profile"),
        field(6, "Why do you want to work here?", tag="textarea", type="textarea"),
        field(7, "Are you legally authorized to work in the United States? *",
              tag="select", type="select-one", required=True,
              options=[{"value": "1", "text": "Yes"}, {"value": "0", "text": "No"}]),
        field(8, "Will you now or in the future require sponsorship? *",
              tag="select", type="select-one", required=True,
              options=[{"value": "1", "text": "Yes"}, {"value": "0", "text": "No"}]),
    ]
    page, report = run(fields, job_row, artifacts, tmp_path)
    filled = {label: value for _, label, value in page.actions}

    assert filled["First Name *"] == "Alex"
    assert filled["Last Name *"] == "Rivera"
    assert filled["Email *"] == "alex@example.com"
    assert filled["Phone"] == "555-0100"
    assert filled["Resume/CV *"].endswith("resume.pdf")
    assert filled["LinkedIn Profile"] == "https://linkedin.com/in/alex"
    assert "billing platform" in filled["Why do you want to work here?"]
    assert filled["Are you legally authorized to work in the United States? *"] == "Yes"
    assert filled["Will you now or in the future require sponsorship? *"] == "No"
    assert not report.required_empty
    assert not report.error


def test_never_touches_consent_credentials_or_identity_numbers(job_row, artifacts, tmp_path):
    fields = [
        field(0, "Password", type="password"),
        field(1, "Social Security Number"),
        field(2, "Date of Birth"),
        field(3, "I agree to the terms and conditions", type="checkbox"),
        field(4, "Subscribe me to job alerts", type="checkbox"),
    ]
    page, report = run(fields, job_row, artifacts, tmp_path)
    assert page.actions == []
    skipped = {s["label"] for s in report.skipped}
    assert "Password" in skipped and "Social Security Number" in skipped


def test_leaves_prefilled_values_alone(job_row, artifacts, tmp_path):
    fields = [field(0, "Email *", type="email", required=True)]
    fields[0]["value"] = "already@there.com"
    page, report = run(fields, job_row, artifacts, tmp_path)
    assert page.actions == []


def test_radio_group_picks_the_matching_option(job_row, artifacts, tmp_path):
    fields = [
        field(0, "Are you legally authorized to work in the US?", type="radio", name="auth"),
        field(1, "Are you legally authorized to work in the US?", type="radio", name="auth"),
    ]
    radio_options = {"auth": [{"idx": "0", "text": "Yes"}, {"idx": "1", "text": "No"}]}
    page, report = run(fields, job_row, artifacts, tmp_path, radio_options)
    assert ("check", "Are you legally authorized to work in the US?", True) in page.actions
    assert page.by_idx["0"]["checked"] is True
    assert page.by_idx["1"]["checked"] is False


def test_reports_required_fields_it_could_not_answer(job_row, artifacts, tmp_path):
    fields = [
        field(0, "Email *", type="email", required=True),
        field(1, "Describe a time you disagreed with your engineering lead *",
              tag="textarea", type="textarea", required=True),
    ]
    page, report = run(fields, job_row, artifacts, tmp_path)
    labels = [r["label"] for r in report.required_empty]
    assert any("disagreed" in label for label in labels)
    assert not any("Email" in label for label in labels)


def test_cover_letter_goes_in_the_cover_letter_box(job_row, artifacts, tmp_path):
    fields = [field(0, "Cover Letter", tag="textarea", type="textarea")]
    page, report = run(fields, job_row, artifacts, tmp_path)
    assert ("fill", "Cover Letter", "Short letter body.") in page.actions


def test_cover_letter_file_input_uploads_the_letter_not_the_resume(job_row, artifacts, tmp_path):
    fields = [
        field(0, "Resume", type="file"),
        field(1, "Cover Letter (optional)", type="file"),
    ]
    page, report = run(fields, job_row, artifacts, tmp_path)
    uploads = {label: path for action, label, path in page.actions if action == "upload"}
    assert uploads["Resume"].endswith("resume.pdf")
    assert uploads["Cover Letter (optional)"].endswith("cover_letter.txt")


def test_a_broken_page_reports_instead_of_raising(job_row, artifacts, tmp_path):
    class Exploding(FakePage):
        def evaluate(self, js, arg=None):
            raise RuntimeError("navigation happened")

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF")
    report = Filler(PROFILE, resume).fill_page(Exploding([]), job_row, artifacts)
    assert "navigation happened" in report.error


def test_visa_question_as_a_text_box_gets_the_sentence_not_yes(job_row, artifacts, tmp_path):
    profile = Profile(raw={
        **PROFILE.raw,
        "eligibility": {"authorized_to_work": True, "requires_sponsorship": True,
                        "status_note": "I am on an H1B with an approved I-140 and "
                                       "would need an H1B transfer."},
    })
    fields = [
        field(0, "Describe your current work authorization status",
              tag="textarea", type="textarea"),
        field(1, "Will you now or in the future require sponsorship?",
              tag="select", type="select-one",
              options=[{"value": "1", "text": "Yes"}, {"value": "0", "text": "No"}]),
    ]
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF")
    page = FakePage(fields)
    Filler(profile, resume).fill_page(page, job_row, artifacts)
    filled = {label: value for _, label, value in page.actions}
    assert "approved I-140" in filled["Describe your current work authorization status"]
    # the dropdown still gets the plain answer
    assert filled["Will you now or in the future require sponsorship?"] == "Yes"
