"""Fill an application form and then stop.

The agent never clicks submit. It fills what it can prove an answer for, leaves
everything else alone, and prints a report of what it filled, what it skipped and
which required fields are still empty so you know exactly what to check before
you click.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import fieldmap
from .fieldmap import CHOICE, LONGTEXT, TEXT, Rule

log = logging.getLogger(__name__)

# Pull every visible, enabled control on the page and describe it well enough to
# match against the rules. Runs in the page, returns plain json.
COLLECT_JS = r"""
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0'
           && (r.width > 0 || r.height > 0 || el.type === 'file');
  };
  const labelFor = (el) => {
    const bits = [];
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) bits.push(l.innerText);
    }
    const wrap = el.closest('label');
    if (wrap) bits.push(wrap.innerText);
    if (!bits.length) {
      // Greenhouse, Lever and Ashby all wrap a field in a div that also holds
      // its question text. Walk up a few levels and take the shortest useful one.
      let node = el.parentElement, hops = 0;
      while (node && hops < 4) {
        const t = (node.innerText || '').trim();
        if (t && t.length < 400) { bits.push(t); break; }
        node = node.parentElement; hops++;
      }
    }
    for (const attr of ['aria-label', 'placeholder', 'name', 'id']) {
      const v = el.getAttribute(attr);
      if (v) bits.push(v.replace(/[_\-\[\]]+/g, ' '));
    }
    const fieldset = el.closest('fieldset');
    if (fieldset) {
      const lg = fieldset.querySelector('legend');
      if (lg) bits.push(lg.innerText);
    }
    return bits.join(' | ').replace(/\s+/g, ' ').trim();
  };

  const out = [];
  let idx = 0;
  document.querySelectorAll('input, select, textarea').forEach((el) => {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) return;
    if (el.disabled || el.readOnly) return;
    if (type !== 'file' && !visible(el)) return;
    el.setAttribute('data-jobagent-idx', String(idx));
    const rec = {
      idx, type,
      tag: el.tagName.toLowerCase(),
      name: el.name || '',
      label: labelFor(el),
      required: el.required || el.getAttribute('aria-required') === 'true',
      value: el.value || '',
      checked: el.checked || false,
    };
    if (el.tagName.toLowerCase() === 'select') {
      rec.options = Array.from(el.options).map(o => ({ value: o.value, text: o.text.trim() }));
    }
    out.push(rec);
    idx++;
  });
  return out;
}
"""

APPLY_BUTTON_TEXTS = [
    "apply for this job", "apply now", "apply to this job", "submit application",
    "i'm interested", "apply",
]
SUBMIT_WORDS = re.compile(r"\b(submit|send application)\b", re.I)


@dataclass
class FillReport:
    job_id: str
    url: str
    ats: str = ""
    filled: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)
    required_empty: list[dict] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id, "url": self.url, "ats": self.ats,
            "filled": self.filled, "skipped": self.skipped,
            "unmatched": self.unmatched, "required_empty": self.required_empty,
            "error": self.error,
        }

    def summary(self) -> str:
        parts = [f"filled {len(self.filled)}"]
        if self.required_empty:
            parts.append(f"{len(self.required_empty)} required still empty")
        if self.unmatched:
            parts.append(f"{len(self.unmatched)} fields we had no answer for")
        if self.error:
            parts.append(f"error: {self.error}")
        return ", ".join(parts)


def _best_option(options: list[dict], want: str) -> str | None:
    """Pick the select option that best expresses the answer we want."""
    if not options or not want:
        return None
    texts = [o["text"] for o in options if o["text"].strip()]
    want_l = want.strip().lower()

    for opt in options:                       # exact
        if opt["text"].strip().lower() == want_l:
            return opt["text"]
    if want_l in ("yes", "no"):               # yes and no need word boundaries
        pattern = re.compile(rf"^\s*{want_l}\b", re.I)
        for opt in options:
            if pattern.match(opt["text"]):
                return opt["text"]
    for opt in options:                       # substring either way
        text_l = opt["text"].strip().lower()
        if text_l and (want_l in text_l or text_l in want_l):
            return opt["text"]
    close = difflib.get_close_matches(want, texts, n=1, cutoff=0.72)
    return close[0] if close else None


def _question_answer(label: str, answers: list[dict]) -> str | None:
    """Fuzzy match a form question to one of the answers we generated."""
    if not answers or not label:
        return None
    label_l = label.lower()
    best, best_score = None, 0.0
    for item in answers:
        question_l = item["question"].lower()
        ratio = difflib.SequenceMatcher(None, label_l, question_l).ratio()
        # Content word overlap catches "Tell us about a product you shipped"
        # against "Describe a product you shipped and the outcome it drove",
        # which raw string similarity scores far too low. Two shared words is
        # the floor, otherwise a single common word matches everything.
        a = set(re.findall(r"[a-z]{4,}", label_l))
        b = set(re.findall(r"[a-z]{4,}", question_l))
        shared = a & b
        overlap = len(shared) / max(1, min(len(a), len(b))) if len(shared) >= 2 else 0.0
        score = max(ratio, overlap)
        if score > best_score:
            best, best_score = item["answer"], score
    return best if best_score >= 0.5 else None


def open_application_form(page) -> None:
    """Some boards show the posting first and the form behind an Apply button."""
    if page.locator("input[type=file], input[name*=email i], input[type=email]").count():
        return
    for text in APPLY_BUTTON_TEXTS:
        button = page.get_by_role("button", name=re.compile(text, re.I))
        link = page.get_by_role("link", name=re.compile(text, re.I))
        for candidate in (button, link):
            try:
                if candidate.count():
                    candidate.first.click(timeout=5000)
                    page.wait_for_timeout(2500)
                    return
            except Exception:
                continue


class Filler:
    def __init__(self, profile, resume_path: Path):
        self.profile = profile
        self.resume_path = str(resume_path)
        self.rules: list[Rule] = fieldmap.build_rules(profile, self.resume_path)

    def fill_page(self, page, job_row, artifacts: dict[str, str]) -> FillReport:
        report = FillReport(job_id=job_row["id"], url=job_row["url"], ats=job_row["ats"] or "")
        answers: list[dict] = []
        if artifacts.get("answers") and Path(artifacts["answers"]).exists():
            answers = json.loads(Path(artifacts["answers"]).read_text())
        cover_letter = ""
        if artifacts.get("cover_letter") and Path(artifacts["cover_letter"]).exists():
            cover_letter = Path(artifacts["cover_letter"]).read_text().strip()

        try:
            open_application_form(page)
            page.wait_for_timeout(1200)
            fields = page.evaluate(COLLECT_JS)
        except Exception as exc:
            report.error = f"could not read the form: {exc}"
            return report

        radio_groups_done: set[str] = set()

        for spec in fields:
            label = spec["label"]
            locator = page.locator(f'[data-jobagent-idx="{spec["idx"]}"]')
            rule = fieldmap.match(self.rules, label)

            if rule and rule.skip:
                report.skipped.append({"label": label[:120], "why": rule.note or rule.key})
                continue

            try:
                filled = self._fill_one(
                    page, locator, spec, rule, answers, cover_letter,
                    artifacts, radio_groups_done,
                )
            except Exception as exc:
                report.skipped.append({"label": label[:120], "why": f"fill failed: {exc}"})
                continue

            if filled is not None:
                report.filled.append({"label": label[:120], "value": str(filled)[:200]})
            elif spec["type"] not in ("radio", "checkbox") or spec["required"]:
                report.unmatched.append(
                    {"label": label[:160], "type": spec["type"], "required": spec["required"]}
                )

        # Re-read the page so "required and still empty" reflects what is really there.
        try:
            for spec in page.evaluate(COLLECT_JS):
                if spec["required"] and not spec["value"] and not spec["checked"]:
                    report.required_empty.append(
                        {"label": spec["label"][:160], "type": spec["type"]}
                    )
        except Exception:
            pass
        return report

    def _fill_one(self, page, locator, spec, rule, answers, cover_letter,
                  artifacts, radio_groups_done) -> str | None:
        kind = spec["type"]
        label = spec["label"]

        # --- file uploads ---
        if kind == "file":
            wants_cover = re.search(r"cover letter", label, re.I)
            if wants_cover:
                path = artifacts.get("cover_letter")
                if not path:
                    return None
            else:
                path = self.resume_path
            locator.set_input_files(path, timeout=15000)
            return Path(path).name

        # --- radio groups: choose the option whose own label matches ---
        if kind == "radio":
            group = spec["name"] or label
            if group in radio_groups_done or not rule:
                return None
            want = rule.resolve()
            if not want:
                return None
            options = page.evaluate(
                """(name) => Array.from(document.querySelectorAll(
                     `input[type=radio][name="${CSS.escape(name)}"]`))
                     .map((el, i) => ({
                        idx: el.getAttribute('data-jobagent-idx'),
                        text: (el.closest('label')?.innerText
                               || document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.innerText
                               || el.value || '').trim()
                     }))""",
                spec["name"],
            )
            choice = _best_option([{"value": o["idx"], "text": o["text"]} for o in options], want)
            if choice is None:
                return None
            target = next(o for o in options if o["text"] == choice)
            page.locator(f'[data-jobagent-idx="{target["idx"]}"]').check(timeout=8000)
            radio_groups_done.add(group)
            return choice

        # --- checkboxes are yours. We never tick a consent or an attestation ---
        if kind == "checkbox":
            return None

        # --- selects ---
        if spec["tag"] == "select":
            if not rule:
                return None
            want = rule.resolve()
            choice = _best_option(spec.get("options", []), want)
            if choice is None:
                return None
            locator.select_option(label=choice, timeout=8000)
            return choice

        # --- free text ---
        value = None
        if rule and rule.kind in (TEXT, CHOICE, LONGTEXT) and rule.key == "cover_letter":
            value = cover_letter
        elif rule and rule.kind in (TEXT, CHOICE, LONGTEXT):
            value = rule.resolve()
        if not value and spec["tag"] == "textarea":
            value = _question_answer(label, answers)
        if not value and kind in ("text", "email", "tel", "url") and not rule:
            value = _question_answer(label, answers)
        if not value:
            return None
        if spec["value"].strip():
            return None                       # the form already had something, leave it
        locator.fill(value, timeout=8000)
        return value
