"""Local deterministic scholarship drafts grounded in verified context files."""

from __future__ import annotations

import re
import os
import argparse
from dataclasses import dataclass
from pathlib import Path

from src.db import ScholarshipDatabase
from src.models import CandidateType, DraftRecord, DraftSource, DraftStatus, Profile, Scholarship, ScholarshipStatus, UserStatus
from src.profile import load_profile
from src.llm import LLMClient, build_llm
from src.config import load_environment


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_DRAFTS_DIR = ROOT / "drafts"


class DraftContextError(ValueError):
    """Raised when required private drafting context is unavailable."""


@dataclass(frozen=True)
class DraftingContext:
    profile: Profile
    story_bank: str
    answers_bank: str
    bot_context: str


@dataclass(frozen=True)
class StoryMaterial:
    angle: str
    draft: str
    shorter: str
    longer: str
    facts: list[str]
    why: str


def load_drafting_context(data_dir: str | Path = DEFAULT_DATA_DIR) -> DraftingContext:
    """Load every required local context source; missing files fail clearly."""

    directory = Path(data_dir)
    paths = {
        "profile": directory / "profile.yaml",
        "story_bank": directory / "story_bank.md",
        "answers_bank": directory / "scholarship_answers_bank.md",
        "bot_context": directory / "bot_context.md",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise DraftContextError("Missing required drafting context: " + ", ".join(missing))
    texts = {
        key: path.read_text(encoding="utf-8")
        for key, path in paths.items()
        if key != "profile"
    }
    if any(not text.strip() for text in texts.values()):
        raise DraftContextError("Drafting context files must not be empty.")
    return DraftingContext(
        profile=load_profile(paths["profile"]),
        story_bank=texts["story_bank"],
        answers_bank=texts["answers_bank"],
        bot_context=texts["bot_context"],
    )


def slugify(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug[:80].rstrip("-") or fallback)


def select_story_angle(prompt: str) -> str:
    """Choose the strongest verified story using explicit prompt signals."""

    text = prompt.lower()
    rules = (
        ("family_hardship", ("family hardship", "immigration", "immigrant", "legal challenge", "adversity at home")),
        ("financial_need", ("financial need", "financial support", "financial hardship", "pay for college", "scholarship help")),
        ("leadership", ("leader", "leadership", "led a team", "team leadership")),
        ("communication_teaching", ("communicat", "teach", "mentor", "inclusion", "adapt")),
        ("service", ("community service", "volunteer", "service", "served", "give back", "community involvement")),
        ("discipline", ("discipline", "resilience", "persever", "challenge", "commitment", "extracurricular")),
        ("research", ("research", "experiment", "computer vision", "academic inquiry")),
        ("backend_software", ("backend", "flask", "mongodb", "api", "web service")),
        ("innovation_technical", ("innovation", "innovative", "technical project", "automation", "etl", "power bi", "data pipeline")),
        ("career_goals", ("career", "computer science", "why cs", "professional goal", "future goal", "aerospace", "software engineering")),
    )
    for angle, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return angle
    return "career_goals"


def _materials(context: DraftingContext) -> dict[str, StoryMaterial]:
    profile = context.profile
    name = profile.preferred_name or profile.full_name
    school = profile.education[0].school
    major = profile.education[0].major
    # These marker checks make the private story bank and answer bank active inputs,
    # while the prose below remains constrained to reviewed, public-safe facts.
    required_markers = ("Bell Textron", "Training Navigator", "Teaching piano", "UTA computer vision")
    if not all(marker.lower() in context.story_bank.lower() for marker in required_markers):
        raise DraftContextError("Story bank is missing one or more required verified story sections.")
    if "financial support" not in context.answers_bank.lower():
        raise DraftContextError("Answers bank is missing the cautious financial-support guidance.")
    if "never claim" not in context.bot_context.lower():
        raise DraftContextError("Bot context is missing drafting safety boundaries.")

    return {
        "career_goals": StoryMaterial(
            angle="Bell Textron and aerospace software",
            draft=(
                f"I am a {major} student at {school} pursuing a career in practical software engineering. "
                "At Bell Textron, I have seen how automation, data pipelines, CI/CD, and analytics support "
                "an aerospace engineering organization. That experience taught me that dependable software "
                "requires more than code: it requires understanding users, data, security, deployment, and "
                "the people who will maintain the system. I want to keep building tools that reduce manual "
                "work and help technical teams make better use of trustworthy information."
            ),
            shorter=(
                f"I am a {major} student at {school} working toward a software engineering career. My Bell "
                "Textron experience with automation, data, CI/CD, and analytics showed me how dependable "
                "software can improve real aerospace engineering workflows."
            ),
            longer=(
                "My coursework gives me the foundations to reason about algorithms, systems, databases, and "
                "software design, while my internships show me how those ideas operate under real constraints. "
                "I hope to continue at the intersection of software, engineering, aerospace, and data-driven "
                "decision-making, building systems that are useful long after their first release."
            ),
            facts=[
                f"{name} studies {major} at {school}.",
                "Bell Textron public role wording: Software Engineer / Digital Enterprise.",
                "Public-safe Bell work includes automation, CI/CD, ETL, reporting, and analytics.",
                "Career interests include dependable software for engineering, aerospace, automation, and data systems.",
            ],
            why="The prompt is best answered by connecting verified CS goals to public-safe aerospace software experience.",
        ),
        "leadership": StoryMaterial(
            angle="Leading 10 interns on Training Navigator",
            draft=(
                "One of my strongest leadership experiences has been leading a group of 10 interns on a Training "
                "Navigator analytics project. The request began broadly: turn scattered training, certification, "
                "and requirement data into something people could maintain and use. I helped connect the user need "
                "to a Python ETL process and Power BI model while giving the team a clearer structure for the work. "
                "The experience taught me that leadership often means reducing ambiguity, communicating priorities, "
                "and helping people move toward a practical result together."
            ),
            shorter=(
                "I led 10 interns on a Training Navigator project that used Python ETL and Power BI to organize "
                "scattered training and certification data. I learned that leadership means reducing ambiguity, "
                "connecting technical work to user needs, and helping a team maintain momentum."
            ),
            longer=(
                "The technical output mattered, but so did the refresh process, data model, documentation, and ability "
                "of non-technical users to understand the result. That pushed me to think beyond completing my own "
                "tasks and toward creating enough clarity for the whole team to contribute effectively."
            ),
            facts=[
                "Led a group of 10 interns.",
                "The Training Navigator project used Python ETL and Power BI.",
                "The project organized training, certification, requirement, and compliance data.",
            ],
            why="The verified Training Navigator experience directly demonstrates leadership through ambiguity and teamwork.",
        ),
        "innovation_technical": StoryMaterial(
            angle="FRACAS notification automation",
            draft=(
                "I am drawn to technical innovation when it turns a repetitive or fragile process into a system people "
                "can trust. At Bell Textron, I designed a Python and Azure DevOps workflow that replaced a manual "
                "morning notification process for a FRACAS data product with a scheduled pipeline. The important part "
                "was not simply automating an email; it was separating the workflow cleanly, handling configuration "
                "safely, and making the process more reliable. That project strengthened my interest in automation "
                "that removes friction while remaining maintainable for the team that inherits it."
            ),
            shorter=(
                "At Bell Textron, I used Python and Azure DevOps to replace a manual morning notification process "
                "with a scheduled pipeline. The project showed me that useful innovation combines automation with "
                "reliability, safe configuration, and maintainability."
            ),
            longer=(
                "I apply the same mindset to ETL and analytics work: understand the existing process, identify failure "
                "points, stabilize the data or workflow, and leave behind something others can operate. Innovation is "
                "most valuable to me when it makes everyday technical work clearer and more dependable."
            ),
            facts=[
                "Built a scheduled notification pipeline using Python and Azure DevOps.",
                "The workflow replaced manual morning execution for a FRACAS data product.",
                "Public discussion must avoid internal hostnames, paths, credentials, IDs, and proprietary details.",
            ],
            why="The FRACAS automation is a concrete, public-safe example of practical technical innovation.",
        ),
        "service": StoryMaterial(
            angle="Kappa Sigma service supporting Mission Arlington",
            draft=(
                "My campus involvement through Kappa Sigma has included community service supporting Mission "
                "Arlington. I value service that is practical and grounded in showing up for work that helps a local "
                "organization serve its community. Because my hours and specific duties are not fully documented, I "
                "would rather describe the experience accurately than exaggerate it. The larger lesson is that "
                "contribution often comes from consistency, humility, and a willingness to support work already in motion."
            ),
            shorter=(
                "Through Kappa Sigma, I participated in community service supporting Mission Arlington. The experience "
                "reinforced that meaningful contribution can be simple: show up consistently, help responsibly, and "
                "support organizations already doing direct local work."
            ),
            longer=(
                "That perspective also shapes how I think about software engineering. I want to build tools that reduce "
                "friction for other people, but useful work begins with listening and understanding what a community or "
                "team actually needs rather than assuming the most visible solution is the best one."
            ),
            facts=[
                "Kappa Sigma membership is verified.",
                "Community service supporting Mission Arlington is verified.",
                "Exact service hours and personally completed tasks are not documented.",
            ],
            why="This is the verified service experience, framed without unsupported hours or duties.",
        ),
        "communication_teaching": StoryMaterial(
            angle="Adaptive piano teaching",
            draft=(
                "Teaching piano taught me that communication is not one-size-fits-all. I studied piano for 10 years "
                "and taught three students, including one deaf student. That experience required me to rethink how I "
                "explained rhythm, gave feedback, and divided a skill into manageable steps. I learned to pay attention "
                "to the learner's response instead of repeating the same explanation. The lesson carries into software "
                "teams, where technical knowledge becomes more valuable when it can be adapted and communicated clearly."
            ),
            shorter=(
                "After studying piano for 10 years, I taught three students, including one deaf student. Adapting my "
                "instruction taught me patience, close observation, and how to explain the same idea in different ways."
            ),
            longer=(
                "Progress was not always immediate, but small improvements accumulated through patience and feedback. "
                "I use that same approach in code reviews, debugging, and team discussions: make the problem smaller, "
                "listen carefully, and adjust the explanation until the next step is clear."
            ),
            facts=[
                "Studied piano for 10 years.",
                "Taught three piano students.",
                "One student was deaf, requiring adaptive communication.",
            ],
            why="The piano story directly demonstrates patient, adaptive communication with a verified outcome.",
        ),
        "discipline": StoryMaterial(
            angle="Long-term discipline through athletics and music",
            draft=(
                "My approach to computer science was shaped by years of activities where improvement depended on "
                "repetition and feedback. I trained in karate for seven years, earned a black belt, and earned 30 "
                "medals. I also played two years of JV and one year of varsity tennis, and went to state twice with "
                "drumline. Those experiences taught me to stay composed under pressure and keep working when progress "
                "is incremental. I bring the same discipline to debugging, difficult coursework, and unfamiliar systems."
            ),
            shorter=(
                "Seven years of karate, varsity tennis, and two state appearances with drumline taught me that progress "
                "comes from repetition, feedback, and consistency. That mindset now guides how I approach difficult "
                "computer science problems."
            ),
            longer=(
                "Earning a karate black belt did not come from one performance; it came from returning to the same "
                "skills until they became dependable. Software development feels similar: patience and disciplined "
                "iteration often matter more than finding an immediate answer."
            ),
            facts=[
                "Karate for seven years; black belt; 30 medals.",
                "Tennis: two years JV and one year varsity.",
                "Went to state with drumline twice.",
            ],
            why="The combined activities provide strong, measurable evidence of sustained discipline and resilience.",
        ),
        "research": StoryMaterial(
            angle="UTA computer vision research",
            draft=(
                "My UTA computer vision research showed me how careful experimentation turns code into evidence. I "
                "worked with Python, OpenCV, stereo-camera calibration, and object detection before I had completed "
                "much upper-division coursework. I built reproducible scripts, tuned detection settings, evaluated a "
                "small hand-collected set, and presented the results to the lab. Reducing reprojection error by about "
                "25 percent taught me to value measurement, reproducibility, and honest evaluation rather than relying "
                "on whether a result merely looks promising."
            ),
            shorter=(
                "In UTA computer vision research, I used Python and OpenCV for stereo-camera calibration and object "
                "detection. Reducing reprojection error by about 25 percent taught me the importance of reproducible "
                "experiments and honest measurement."
            ),
            longer=(
                "The project connected computer science to physical systems and made research feel accessible early in "
                "my education. It gave me confidence to enter unfamiliar technical spaces while staying careful about "
                "what the data actually supports."
            ),
            facts=[
                "UTA research internship used Python, OpenCV, stereo calibration, and YOLOv3.",
                "Reduced reprojection error by about 25 percent.",
                "Presented results to the lab.",
            ],
            why="The UTA experience directly answers research prompts with reproducible technical work and measured results.",
        ),
        "backend_software": StoryMaterial(
            angle="Titan Americas backend internship",
            draft=(
                "At Titan Americas, I helped build a Python, Flask, and MongoDB internal tool intended to reduce manual "
                "status checking across Jira comment threads. I implemented REST endpoints with validation and input "
                "sanitization, improved data integrity with a uniqueness index, and wrote pytest smoke tests for core "
                "routes. The experience showed me that backend engineering is not just exposing endpoints; it is making "
                "data behavior predictable, handling errors consistently, and building something a team can trust."
            ),
            shorter=(
                "At Titan Americas, I built backend features with Python, Flask, and MongoDB, including REST endpoints, "
                "validation, a uniqueness index, and pytest coverage. The work taught me to treat reliability and data "
                "integrity as core software features."
            ),
            longer=(
                "Working on a practical internal workflow also helped me understand the relationship between stakeholder "
                "needs and technical design. A small tool succeeds when it removes friction without creating new confusion "
                "for the people who use and maintain it."
            ),
            facts=[
                "Titan Americas Software Engineering Internship ran May–August 2025.",
                "Used Python, Flask, and MongoDB.",
                "Implemented seven REST endpoints and eight pytest smoke tests.",
            ],
            why="Titan Americas is the strongest verified story for backend, API, Flask, or MongoDB prompts.",
        ),
    }


def _family_material(context: DraftingContext) -> StoryMaterial:
    profile = context.profile
    return StoryMaterial(
        angle="Family hardship skeleton requiring user details",
        draft=(
            "[NEEDS USER INPUT: exact family hardship / immigration context]\n\n"
            "A safe final response can explain the verified situation, the responsibilities or uncertainty it created, "
            "the specific actions I took, and how it shaped my commitment to education. Until those details and "
            "boundaries are provided, I will not add dates, legal circumstances, financial effects, emotions, or outcomes."
        ),
        shorter="[NEEDS USER INPUT: exact family hardship / immigration context]",
        longer=(
            "Suggested structure after review: describe the factual context; identify one concrete challenge; explain "
            "my own response; connect that response to resilience, responsibility, and my path in computer science; "
            "finish with what I hope to build through education."
        ),
        facts=[
            f"{profile.preferred_name or profile.full_name} permits relevant family context only after exact details are supplied.",
            "The profile requires user-provided boundaries before drafting this story.",
        ],
        why="The prompt requests sensitive context that is not documented precisely enough for a factual narrative.",
    )


def _financial_material(context: DraftingContext) -> StoryMaterial:
    profile = context.profile
    return StoryMaterial(
        angle="Cautious education-cost support",
        draft=(
            "Scholarship support would reduce education-cost pressure and help me focus more deeply on coursework, "
            "technical projects, and professional growth. I am pursuing computer science at the University of Texas "
            "at Arlington with the goal of building dependable software for engineering, aerospace, automation, or "
            "data-driven environments. Support would give me more room to keep developing that path through classes, "
            "applied projects, and responsible professional experience."
        ),
        shorter=(
            "Scholarship support would reduce education-cost pressure and help me focus on coursework, technical "
            "projects, and professional growth as I prepare for a software engineering career."
        ),
        longer=(
            "The value of that support would be practical: more attention available for difficult CS coursework, "
            "stronger project work, and continued growth from applied engineering experience. Any financial fields "
            "required by the application should be completed only from reviewed records."
        ),
        facts=[
            f"Studies {profile.education[0].major} at {profile.education[0].school}.",
            "Career goal centers on dependable software in technical environments.",
            "Financial narrative is limited to education-cost pressure and academic/professional focus.",
        ],
        why="This cautious angle answers how support would help without making unsupported hardship statements.",
    )


FORBIDDEN_CLAIM_PATTERNS = (
    r"\bi am (?:a )?low[- ]income\b",
    r"\bmy family is low[- ]income\b",
    r"\bi (?:have )?completed (?:the )?fafsa\b",
    r"\bi am (?:a )?first[- ]generation\b",
    r"\bmy recommendation (?:letter )?is available\b",
    r"\bi (?:completed|performed|volunteered) exactly \d+ (?:service )?hours\b",
    r"\bat mission arlington, i (?:distributed|organized|delivered|served|managed)\b",
    r"\bproprietary (?:bell|internal)\b",
    r"\b(?:volunteered|served|completed|performed) \d+(?:\.\d+)? (?:community |service )?hours\b",
    r"\binternal bell (?:system|project|tool|data|detail)s?\b",
)


def detect_forbidden_claims(text: str) -> list[str]:
    """Return safety patterns found in generated prose without exposing private context."""

    return [pattern for pattern in FORBIDDEN_CLAIM_PATTERNS if re.search(pattern, text, re.IGNORECASE)]


def _validate_safe_answer(*answers: str) -> None:
    combined = "\n".join(answers).lower()
    found = detect_forbidden_claims(combined)
    if found:
        raise ValueError(f"Draft safety check rejected a forbidden claim pattern: {found[0]}")


def _checklist(values: list[str], *, empty: str) -> str:
    items = values or [empty]
    return "\n".join(f"- [ ] {item}" for item in items)


def build_llm_prompt(context: DraftingContext, scholarship: Scholarship, prompt: str) -> str:
    """Build a fully grounded prompt whose safety rules remain visible to tests and providers."""

    profile_text = context.profile.model_dump_json(indent=2, exclude_none=True)
    return f"""Write one tailored scholarship essay draft in a natural, factual voice.
Return only the essay body; do not add headings or commentary.

SCHOLARSHIP
Name: {scholarship.name}
Provider: {scholarship.provider or 'unknown'}
Amount: {scholarship.amount if scholarship.amount is not None else 'unknown'}
Deadline: {scholarship.deadline or 'unknown'}
Eligibility: {scholarship.eligibility}
Prompt: {prompt}

NON-NEGOTIABLE SAFETY RULES
- Never claim low income, FAFSA completion, first-generation status, recommendation availability,
  exact service hours, exact Mission Arlington tasks, proprietary/internal Bell details, or any
  unverified family hardship or immigration detail.
- If hardship or immigration context is needed, write exactly:
  [NEEDS USER INPUT: exact family hardship / immigration context]
- For financial need, use cautious education-cost-pressure language only.
- Use only facts in the supplied local context. Do not infer missing facts.

PROFILE
{profile_text}

STORY BANK
{context.story_bank}

ANSWER BANK
{context.answers_bank}

BOT CONTEXT
{context.bot_context}
"""


def generate_draft(
    scholarship: Scholarship,
    prompt: str,
    *,
    context: DraftingContext | None = None,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    output_root: str | Path = DEFAULT_DRAFTS_DIR,
    llm: LLMClient | None = None,
    require_llm: bool = False,
) -> DraftRecord:
    """Generate and write a grounded Markdown draft for one saved scholarship prompt."""

    if scholarship.id is None:
        raise ValueError("Scholarship must be saved before generating a tracked draft.")
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Prompt must not be empty.")
    context = context or load_drafting_context(data_dir)
    angle_key = select_story_angle(prompt)
    if angle_key == "family_hardship":
        material = _family_material(context)
    elif angle_key == "financial_need":
        material = _financial_material(context)
    else:
        material = _materials(context)[angle_key]

    missing: list[str] = []
    claims_to_verify = ["Confirm the final answer meets the scholarship's exact word or character limit."]
    if angle_key == "family_hardship":
        missing.append("[NEEDS USER INPUT: exact family hardship / immigration context]")
    if angle_key == "service":
        missing.append("Confirm personally completed Mission Arlington tasks if the prompt requires task-level detail.")
    prompt_lower = prompt.lower()
    if angle_key == "financial_need" and (
        scholarship.fafsa_required is True or "fafsa" in prompt_lower or "sai" in prompt_lower
    ):
        missing.append("FAFSA/SAI information is unavailable and must be supplied from reviewed records if required.")
    if scholarship.provider:
        claims_to_verify.append(f"Confirm scholarship-specific references for {scholarship.provider} before submission.")

    draft_answer = material.draft
    generation_source = DraftSource.TEMPLATE_FALLBACK
    generation_error: str | None = None
    provider = llm or build_llm(os.environ)
    if provider.enabled:
        try:
            generated = provider.generate(build_llm_prompt(context, scholarship, prompt))
            if generated:
                _validate_safe_answer(generated)
                draft_answer = generated
                generation_source = DraftSource.AI
        except Exception as exc:
            if require_llm:
                raise RuntimeError(f"OpenAI draft generation failed: {exc}") from exc
            generation_error = str(exc)
            # Network/provider/safety failures deliberately preserve the local template fallback.
            draft_answer = material.draft
    _validate_safe_answer(draft_answer, material.shorter, material.longer)
    scholarship_slug = slugify(scholarship.name, fallback=f"scholarship-{scholarship.id}")
    prompt_slug = slugify(prompt, fallback="prompt")
    path = (Path(output_root) / scholarship_slug / f"{prompt_slug}.md").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    markdown = f"""# {scholarship.name} — Draft

> Draft for human review. Verify every fact and adapt it to the application before submission.

## Prompt

{prompt}

## Draft answer

{draft_answer}

## Shorter version

{material.shorter}

## Longer version

{material.draft}

{material.longer}

## Facts used

{_checklist(material.facts, empty="No profile facts used.")}

## Claims to verify

{_checklist(claims_to_verify, empty="No additional claims identified.")}

## Missing user input

{_checklist(missing, empty="None currently identified.")}

## Why this angle fits the scholarship

{material.why}

## Safety flags

- Draft requires human review and explicit approval before autofill.
- No low-income, FAFSA-completion, first-generation, recommendation-availability, exact-hours,
  exact Mission Arlington task, proprietary Bell, or unverified hardship/immigration claim is permitted.
"""
    path.write_text(markdown, encoding="utf-8")
    return DraftRecord(
        scholarship_id=scholarship.id,
        scholarship_name=scholarship.name,
        prompt=prompt,
        path=path,
        status=(DraftStatus.NEEDS_USER_INPUT if missing else DraftStatus.DRAFT) if generation_source == DraftSource.AI else DraftStatus.NEEDS_REGENERATION,
        story_angle=material.angle,
        facts_used=material.facts,
        claims_to_verify=claims_to_verify,
        missing_user_input=missing,
        why_angle_fits=material.why,
        generation_source=generation_source,
        generation_error=generation_error,
    )


def generate_and_save_draft(
    scholarship: Scholarship,
    prompt: str,
    *,
    database: ScholarshipDatabase,
    context: DraftingContext | None = None,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    output_root: str | Path = DEFAULT_DRAFTS_DIR,
    llm: LLMClient | None = None,
    require_llm: bool = False,
) -> DraftRecord:
    """Generate the Markdown file and upsert its review metadata in SQLite."""

    draft = generate_draft(
        scholarship,
        prompt,
        context=context,
        data_dir=data_dir,
        output_root=output_root,
        llm=llm,
        require_llm=require_llm,
    )
    return database.save_draft(draft)


def generate_for_scholarship(scholarship: Scholarship, database: ScholarshipDatabase, *, llm: LLMClient | None = None, max_prompts: int | None = None, require_llm: bool = False) -> list[DraftRecord]:
    """Generate every safe prompted answer for an explicitly selected scholarship."""
    if scholarship.user_status in {UserStatus.REJECTED, UserStatus.JUNK}:
        raise ValueError("Rejected or junk candidates cannot generate drafts.")
    if not scholarship.essay_prompts:
        return []
    generated: list[DraftRecord] = []
    for prompt in scholarship.essay_prompts[:max_prompts]:
        if any(term in prompt.lower() for term in ("immigration", "family hardship", "family income", "exact service hours")):
            continue
        generated.append(generate_and_save_draft(scholarship, prompt, database=database, llm=llm, require_llm=require_llm))
    return generated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate safe, human-reviewed scholarship drafts")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("generate")
    one.add_argument("--scholarship-id", type=int, required=True)
    sub.add_parser("generate-approved")
    top = sub.add_parser("generate-top-review")
    top.add_argument("--limit", type=int, default=10)
    regen = sub.add_parser("regenerate-fallbacks")
    regen.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    load_environment()
    configured = Path(os.getenv("SCHOLARSHIP_DB_PATH", "data/scholarships.db"))
    database = ScholarshipDatabase(configured if configured.is_absolute() else ROOT / configured)
    database.initialize()
    llm = build_llm()
    if not llm.enabled:
        print("OpenAI is not configured; template fallback will be used.")
    records = database.list_scholarships()
    fallback_drafts: list[DraftRecord] = []
    if args.command == "generate":
        selected = [r for r in records if r.id == args.scholarship_id]
        if not selected: raise SystemExit(f"Scholarship not found: {args.scholarship_id}")
    elif args.command == "generate-approved":
        selected = [r for r in records if r.user_status == UserStatus.APPROVED_FOR_APPLY and r.essay_prompts]
    elif args.command == "generate-top-review":
        selected = [r for r in records if r.essay_prompts and r.candidate_type in {CandidateType.DIRECT_APPLICATION, CandidateType.DETAIL_PAGE} and r.user_status not in {UserStatus.REJECTED, UserStatus.JUNK} and r.status in {ScholarshipStatus.MAYBE, ScholarshipStatus.MANUAL_REVIEW}]
        selected.sort(key=lambda r: ((r.ranking.total_score if r.ranking else 0), r.confidence_score), reverse=True)
        selected = selected[:args.limit]
    else:
        fallback_drafts = [d for d in database.list_drafts() if d.generation_source == DraftSource.TEMPLATE_FALLBACK][:args.limit]
        ids = {d.scholarship_id for d in fallback_drafts}
        selected = [r for r in records if r.id in ids and r.user_status not in {UserStatus.REJECTED, UserStatus.JUNK}]
    drafts: list[DraftRecord] = []
    for record in selected:
        try:
            drafts.extend(generate_for_scholarship(record, database, llm=llm, max_prompts=1 if args.command == "generate-top-review" else None, require_llm=llm.enabled))
        except Exception as exc:
            print(f"Skipped {record.name}: {exc}")
    verb = "Regenerated" if args.command == "regenerate-fallbacks" else "Generated"
    print(f"{verb} {len(drafts)} draft(s) from {len(selected)} selected scholarship(s) using {llm.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
