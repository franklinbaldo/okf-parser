"""Generated Pydantic models from OKF schema contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtremelyLongConceptTypeSegmeH2d941d5c72(BaseModel):
    """Generated Pydantic model for one OKF contract object."""

    model_config = ConfigDict(extra="allow")

    child_segment_segment_8ecca99b72: str = Field(
        alias=(
            "child-segment-segment-segment-segment-segment-segment-segment-"
            "segment-segment-segment-segment-segment-value"
        ),
    )


class ExtremelyLongConceptTypeSegmeH66adf5726d(BaseModel):
    """Generated Pydantic model for one OKF contract object."""

    model_config = ConfigDict(extra="allow")

    customer_identifier_i_450f809f0f: str = Field(
        alias=(
            "customer-identifier-identifier-identifier-identifier-identifie"
            "r-identifier-identifier-identifier-identifier-identifier-ident"
            "ifier-identifier-identifier-identifier-value"
        ),
    )
    description: str = Field(default=None)
    nested_structure_stru_b50584977c: ExtremelyLongConceptTypeSegmeH2d941d5c72 = Field(
        alias=(
            "nested-structure-structure-structure-structure-structure-struc"
            "ture-structure-structure-structure-structure-structure-structu"
            "re-value"
        ),
    )
    title: str = Field(default=None)
    type: Literal[
        "ExtremelyLongConceptTypeSegmentSegmentSegmentSegmentSegmentSeg"
        "mentSegmentSegmentSegmentSegmentSegmentSegmentSegmentSegmentSe"
        "gmentSegmentSegmentSegment",
    ]
