import re

from jobs_status_manager.agent.tools import definitions


def test_update_status_definition_exposes_structured_fact_guidance() -> None:
    definition = next(item for item in definitions() if item.name == "UpdateApplicationStatus")
    tokens = set(re.findall(r"[A-Za-z_]+", definition.description))

    assert {
        "company",
        "department",
        "position",
        "status",
        "interview_round",
        "INTERVIEW",
        "positive",
        "omit",
        "infer",
        "clarification",
    } <= tokens
