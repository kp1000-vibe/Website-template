# jobagent

Finds product management jobs posted in the last few days, scores them against
your resume with Claude, writes the cover letter and the screening answers, then
opens each application in your own Chrome with every field already filled.

It never clicks submit. You read the form and you click submit.

```
  source  ->  score  ->  queue  ->  prep  ->  prefill  ->  you
  boards      Claude     top 5     letter    your Chrome   submit
```

## Why it works this way

Three decisions, all deliberate.

**It reads public ATS apis, not scraped search pages.** Greenhouse, Lever, Ashby
and SmartRecruiters all publish an unauthenticated json feed per company board.
That gives real posting dates, the full description, and no scraping. LinkedIn
and Indeed are not scraped at all. When you find something there yourself, paste
the url into `config/manual_urls.txt` and it joins the same pipeline.

**It never submits.** Auto submit is how accounts get restricted and how a wrong
answer to a sponsorship question goes out under your name. The agent fills the
form and hands it to you. Filling is 95 percent of the work and none of the risk.

**Quality sets the number, not a quota.** `daily_target` is a ceiling. On a thin
day you get two, and two good applications beat five bad ones.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # only needed if Chrome is not already installed

cp config/config.example.yaml config/config.yaml
cp config/profile.example.yaml config/profile.yaml
cp config/manual_urls.txt.example config/manual_urls.txt
$EDITOR config/profile.yaml        # this is the important one

export ANTHROPIC_API_KEY=sk-ant-...   # or run `ant auth login`

python -m jobagent doctor          # tells you exactly what is still missing
python -m jobagent verify-boards   # prunes dead company boards, takes a minute
```

`config/profile.yaml` is gitignored and stays on your machine. Fill it carefully.
Two fields decide more applications than anything else you write:
`eligibility.authorized_to_work` and `eligibility.requires_sponsorship`.

`score`, `prep`, `prefill` and `run` refuse to start while any required field is
still `FILL_ME`. A blank sponsorship answer going out under your name is worse
than no application at all.

### Chrome

The agent drives Chrome through the remote debugging port. Chrome 136 and later
refuse that against your normal profile, so it uses a dedicated one at
`~/.jobagent/chrome-profile`. Start it once and log into whatever you need:

```bash
python -m jobagent chrome
```

That window is a real browser. The sessions persist, and the agent opens its tabs
in it. Your everyday Chrome is untouched.

## Daily use

```bash
python -m jobagent run       # source, score, queue, prep, prefill
python -m jobagent review    # dashboard at http://127.0.0.1:8765
```

The dashboard shows each job with its score, why it fits, what the gaps are, the
generated cover letter and answers, and a report of exactly which fields were
filled and which required ones still need you. Buttons per job: prefill in
Chrome, mark as submitted, skip.

Run a stage on its own when you want to:

```bash
python -m jobagent source              # just pull new postings
python -m jobagent score --offline     # keyword fallback, no api calls
python -m jobagent queue               # pick today's shortlist
python -m jobagent prefill <job_id>    # fill one specific application
python -m jobagent status              # what is in the pipeline
python -m jobagent export --out applications.csv
```

### Running it every morning

macOS or Linux, source and score at 7am, leave the prefilling for when you sit
down:

```cron
0 7 * * 1-5 cd /path/to/jobagent && .venv/bin/python -m jobagent run --no-prefill
```

## What each stage does

**source** hits every board in `config/boards.yaml` concurrently, normalizes the
postings, and applies the cheap filters: is the title actually product management
(product marketing and technical program manager are excluded), is the level one
you would take (`seniority_allow`, which drops associate and vp roles before you
pay to score them), was it posted inside `max_age_days`, is the location one you
can work, is there a real description. A dead board is logged and skipped, never
fatal.

**score** sends each surviving posting to Claude with your resume and constraints
in a cached system prompt, and gets back a structured verdict: a 0 to 100 score,
a one line pitch, the specific overlaps, the specific gaps, and red flags like a
clearance requirement. The prompt is written to be stingy. Most postings should
land between 40 and 70.

**queue** takes the top scorers above `min_score`, caps at one per company per
day, drops anything red flagged for clearance or sponsorship, and stops at
`daily_target`.

**prep** writes the cover letter, answers your configured screening questions, and
produces a `tailoring_notes.md` saying what to emphasise on your resume for that
specific posting. It is told, in the system prompt, never to invent a job, a
metric or a tool that is not on your resume.

**prefill** opens each posting in your Chrome, finds the form, matches every field
against the rules in `jobagent/fill/fieldmap.py`, and fills what it can prove an
answer for. Then it stops.

## What it deliberately will not fill

* passwords and account creation fields
* social security number, date of birth, national id
* consent checkboxes, terms and conditions, marketing opt ins
* any checkbox at all
* anything it does not have a confident answer for, which it lists in the report
  instead of guessing

EEO questions are answered exactly as written in your `profile.yaml`. The default
is decline to answer on all four, which is a complete and normal answer.

## Your resume file is never rewritten

The pdf you point at in `profile.yaml` is what gets uploaded. Machine rebuilt
resumes look worse than the original and lose the formatting you chose. What the
agent gives you instead is `tailoring_notes.md` per job, listing the keywords the
posting screens on that are missing from your resume but are things you have
genuinely done. Editing the resume stays your call.

## Cost

At `claude-opus-5`, a normal day scores roughly 40 to 80 postings and preps 5.
That is a few dollars a day.

The system prompt carrying your resume is byte identical across every call in a
run and is marked for caching, but a one page resume lands around 1,800 tokens,
which may sit under the minimum cacheable prefix. Treat the cache as a bonus, not
as the budget.

To spend less, set `llm.model: claude-sonnet-5` in `config.yaml`, or lower
`llm.max_scored_per_run`.

## Boards

`config/boards.yaml` ships with about 90 company tokens. **It is a starting list,
not a verified one.** Tokens change and companies switch ATS. Run
`python -m jobagent verify-boards` before your first real run and monthly after;
it pings every token and writes the live ones to `boards.verified.yaml`, which
takes precedence.

Adding a company is one line. Open its careers page and read the url:

| url looks like | put the token under |
| --- | --- |
| `job-boards.greenhouse.io/TOKEN` | `greenhouse` |
| `jobs.lever.co/TOKEN` | `lever` |
| `jobs.ashbyhq.com/TOKEN` | `ashby` |
| `jobs.smartrecruiters.com/TOKEN` | `smartrecruiters` |

Workday and iCIMS have no public feed. Put those postings in
`config/manual_urls.txt` and they get scored and prefilled like everything else.
Workday forms are a react application with a multi step wizard, so expect the
filler to get the first page and leave you the rest.

## Tests

```bash
python -m pytest tests -q
```

35 tests covering the title, seniority and location filters, the store dedup, the daily
pick, every source parser, the field matching rules, and the filler driven
against a fake page. The filler tests are the ones to keep green: they assert
that sponsorship and work authorization are answered correctly, and that
passwords, identity numbers and consent boxes are never touched.

The live board apis and a real browser are not reachable from a sandbox, so
sourcing is tested against recorded payload shapes and filling against a fake
page. Run `doctor`, `verify-boards` and one `run` locally before you trust it.

## Layout

```
jobagent/
  cli.py            command line
  pipeline.py       the five stages
  config.py         config and profile loading
  store.py          sqlite: seen jobs, scores, application status
  filters.py        title, freshness, location rules
  scoring.py        Claude fit assessment
  prep.py           cover letter, answers, tailoring notes
  resume.py         pdf and docx to text
  sources/          greenhouse, lever, ashby, smartrecruiters, manual urls
  fill/
    browser.py      Chrome over the debugging port
    fieldmap.py     label to answer rules
    filler.py       fill the form, never submit
  review/           local dashboard
config/             your config, profile and board list
data/               sqlite database, gitignored
runs/               generated letters and answers per job, gitignored
```

## A note on terms of service

Everything polled here is a public, unauthenticated api that companies publish so
their jobs get found. The agent identifies itself in the user agent, fetches each
board once per run, and does not scrape LinkedIn or Indeed. It submits nothing on
its own. Keep it that way and you are doing what a person with a bookmark folder
does, faster.
