from __future__ import annotations

import ibis
import pytest

from okf_parser.relational_contract import (
    RelationalContractError,
    parse_relational_contract,
    validate_relational_contract,
)


CONTRACT = """
[[key]]
type = "Regra"
fields = ["nome"]

[[key]]
type = "Fundamentacao"
fields = ["id"]

[[key]]
type = "Requisito"
fields = ["id"]

[[reference]]
from_type = "Fundamentacao"
from_fields = ["regra"]
to_type = "Regra"
to_fields = ["nome"]

[[reference]]
from_type = "Requisito"
from_fields = ["fundamentacao"]
to_type = "Fundamentacao"
to_fields = ["id"]
"""


def _relations(*, duplicate_rule: bool = False, orphan_requirement: bool = False):
    regras = ibis.memtable(
        {
            "nome": ["aposentadoria", "aposentadoria" if duplicate_rule else "pensao"],
        }
    )
    fundamentacoes = ibis.memtable(
        {
            "id": ["f-1", "f-2", "f-3"],
            "regra": ["aposentadoria", "aposentadoria", "pensao"],
        }
    )
    requisitos = ibis.memtable(
        {
            "id": ["r-1", "r-2", "r-3"],
            "fundamentacao": ["f-1", "f-1", "missing" if orphan_requirement else "f-3"],
        }
    )
    tables = {
        "Regra": regras,
        "Fundamentacao": fundamentacoes,
        "Requisito": requisitos,
    }
    return tables


def test_parse_contract_models_one_to_many_to_many_without_domain_code() -> None:
    contract = parse_relational_contract(CONTRACT)

    assert [(item.concept_type, item.fields) for item in contract.keys] == [
        ("Regra", ("nome",)),
        ("Fundamentacao", ("id",)),
        ("Requisito", ("id",)),
    ]
    assert contract.references[0].from_type == "Fundamentacao"
    assert contract.references[1].to_type == "Fundamentacao"


def test_reference_target_must_be_declared_key() -> None:
    text = """
[[reference]]
from_type = "Filho"
from_fields = ["pai"]
to_type = "Pai"
to_fields = ["id"]
"""
    with pytest.raises(RelationalContractError, match="not a declared"):
        parse_relational_contract(text)


def test_valid_one_to_many_to_many_has_no_relational_diagnostics() -> None:
    contract = parse_relational_contract(CONTRACT)
    tables = _relations()

    diagnostics = validate_relational_contract(
        contract,
        relation=tables.__getitem__,
        available_types=set(tables),
    )

    assert diagnostics == ()


def test_duplicate_key_and_orphan_reference_are_reported() -> None:
    contract = parse_relational_contract(CONTRACT)
    tables = _relations(duplicate_rule=True, orphan_requirement=True)

    diagnostics = validate_relational_contract(
        contract,
        relation=tables.__getitem__,
        available_types=set(tables),
    )
    codes = {item.code for item in diagnostics}

    assert "relational-duplicate-key" in codes
    assert "relational-orphan-reference" in codes
