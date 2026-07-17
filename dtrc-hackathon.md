# AI Agents for Research Hackathon
*Digital Transformation Research Corps*

This hackathon invites participants to build AI agents that help researchers do real work:
find and synthesize knowledge, analyze data, manage lab or study workflows, improve
reproducibility, prepare research materials, or connect specialized tools into a usable
research assistant.

The goal is not to build the flashiest chatbot. The goal is to build an agent that a
researcher could imagine using next week.

## What Teams Should Build

Teams should create an agent that supports a concrete research workflow. Strong projects
will have a clear target user, such as a faculty investigator, graduate student, lab
manager, clinician-researcher, research administrator, librarian, or data analyst.

Good agents may help with tasks such as:

- literature discovery, summarization, or evidence comparison
- dataset exploration, analysis planning, or result interpretation
- lab operations, protocol support, sample tracking, or meeting preparation
- grant, IRB, compliance, or documentation workflows
- clinical or translational research support with appropriate safeguards
- connecting WashU researchers to external APIs, databases, files, or campus resources

## Rubric

Total: 100 points across four categories. Because this hackathon also stress-tests the
starter repo we hand to future cohorts, repo feedback is scored as a primary category
alongside the agent itself.

| Category | Points | What Judges Should Look For |
|---|---:|---|
| Research Impact | 30 | Solves a real researcher pain point; has a clear target user; saves time, improves quality, or enables a useful workflow. |
| Repo Feedback and Bug Reports | 25 | Surfaces real defects or improvements in the starter repo; findings are reproducible and actionable (repro steps, expected vs. actual behavior, environment) and filed as GitHub issues or PRs; quality and impact matter more than raw count. |
| Trust, Safety, and Responsible AI | 25 | Handles uncertainty; avoids overclaiming; protects private data and credentials; cites, preserves, or explains sources when relevant; makes limitations clear. |
| Integration and Usability | 20 | Deploys and runs in our system: follows the AgenticNetwork / A2A structure from the starter repo, exposes clear skills and examples, returns useful structured output, and includes the setup and configuration our team needs to deploy it. |

### Scoring repo feedback

Repo feedback is the least familiar dimension to score, so as a guide:

| Points | What Earns It |
|---:|---|
| 1–8 | Minor or surface findings: small doc fixes, typos, cosmetic friction; reported clearly enough to act on. |
| 9–17 | Solid, reproducible bugs, or a genuinely useful set of improvement suggestions; includes clear repro steps, expected vs. actual behavior, and environment. |
| 18–25 | High-impact findings: setup-breaking or blocking bugs, credential/security gaps in the template, or fixes contributed back as merged PRs; insights that would help future cohorts, not just this team. |

Judges credit valid, actionable findings — steps to reproduce, expected vs. actual
behavior, and specific suggestions — not volume.

## Minimum Expectations

Before scoring, submissions should meet these baseline expectations:

- The agent can be deployed and run in our system, following the AgenticNetwork / A2A structure from the starter repo.
- The repository documents who the agent is for and what workflow it improves.
- No API keys, tokens, passwords, or private research data are committed or exposed.
- The agent does not fabricate citations, evidence, or capabilities.
- The agent is designed to handle at least one ambiguous input, missing input, or failure case.
- It produces a clear human-readable answer and, where appropriate, structured data
  another agent or tool could reuse.

## Submission

There should be only two people in your team. Each team submits a GitHub repository — there is no live demo, and you do not need to
include proof that the agent works. Reviewers read the repositories to understand what each
agent does and to check that it can be deployed in our system. Selected agents will then be
deployed and run by our team, so your agent has to actually work when deployed in the
AgenticNetwork. Make the README the front door. You will be able to work on this agent until 6:00 PM, Wednesday, July 22nd. 

Please email and share your github repo with mdan@wustl.edu and adith@wustl.edu. Both Dan and Adith will need to be able to clone your repo.

Your README should clearly answer:

1. What research workflow does the agent improve?
2. Who at WashU would benefit from it?
3. What does the agent do that a general chatbot would not?
4. What is the agent designed to be able to do — the tasks or prompts it should handle well?
5. What tools, files, APIs, databases, or other agents does it use?
6. How does it handle uncertainty, privacy, credentials, and limitations?

Your repository must be deployable in our system:

- It follows the AgenticNetwork / A2A structure from the starter repo, with the required
  skills, configuration, and entry points in place.
- It includes the setup and configuration our team needs to deploy and run it
  (dependencies, environment variables, and any required credentials).
- Secrets are supplied through configuration, not committed — provide a `.env.example` or
  equivalent instead of real keys.

And, for the Repo Feedback score:

- A short account of the friction or bugs you hit in the starter repo, with links to the
  issues or PRs you filed.

---

A great agent should not only answer a prompt; it should help a researcher move a real task
forward. Winning projects should feel useful, trustworthy, and integrated.

There will be prizes for the top two teams. 

Please direct any questions to mdan@wustl.edu and adith@wustl.edu. 
