"""Pydantic models shared by every LLM task.

Must stay at module scope: common-ai resolves ``output_type`` for XCom
deserialization by walking the loaded DAG, which fails for a class defined
inside a closure.

``Answer`` is the single output contract for every benchmark arm.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

RelationType = Literal["CUSTOMER_LINK", "ACCESS", "MEMBER", "GRANTS"]


class Triple(BaseModel):
    """One relationship stated by one document."""

    subject: str = Field(description="Name of the first entity exactly as written in the text")
    relation: RelationType = Field(
        description=(
            "CUSTOMER_LINK for two accounts that are linked, related or part of the same "
            "organisation; ACCESS for an account GRANTED access to a resource; MEMBER for an "
            "account belonging to a group; GRANTS for a group granting access to a resource"
        )
    )
    obj: str = Field(description="Name of the second entity exactly as written in the text")
    permission: Optional[str] = Field(
        default=None, description="READ, WRITE or ADMIN when the text states one"
    )
    evidence: str = Field(description="The short phrase in the document that states this")


class EntityAttribute(BaseModel):
    """One property of one entity stated by one document."""

    entity: str = Field(description="Entity name exactly as written in the text")
    key: Literal["region", "tier", "status", "resource_type", "sensitivity", "role"]
    value: str


class ExtractedTriples(BaseModel):
    """What one document batch yields. Zero of each is valid: distractor documents may state no facts."""

    triples: List[Triple] = Field(default_factory=list)
    attributes: List[EntityAttribute] = Field(default_factory=list)


class Answer(BaseModel):
    """The one output contract every benchmark arm returns."""

    entity_ids: List[str] = Field(
        default_factory=list,
        description=(
            "The entities that answer the question, by name or id. Empty if the question "
            "asks for a count or a single value rather than a set."
        ),
    )
    scalar: Optional[str] = Field(
        default=None, description="The answer when it is a count or a single value"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    citations: List[str] = Field(
        default_factory=list, description="Document ids or Gremlin queries used"
    )


def as_answer(value) -> Answer:
    """Coerce an XCom payload back into an Answer.

    Handles both a live Answer instance (Airflow 3.3+) and the plain dict
    an older core or a direct DB read produces.
    """
    if isinstance(value, Answer):
        return value
    if isinstance(value, dict):
        # Airflow's serde envelope: {"__data__": {...}, "__classname__": "..."}
        if "__data__" in value and "__classname__" in value:
            value = value["__data__"]
        return Answer.model_validate(value)
    if value is None:
        return Answer()
    return Answer(scalar=str(value))
