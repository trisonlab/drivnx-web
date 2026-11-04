"""Every M1 entity saved and read back through the artifact service, CFX-APP-first-slice AC-35."""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from artifacts.profiles import (
    NewFinding,
    NewObservation,
    add_finding,
    add_observations,
    read_finding,
    read_observations,
    read_profile_run,
    start_profile_run,
)
from artifacts.review import add_contract, read_contract
from artifacts.settings import WorkspaceSettings, read_settings, save_settings
from artifacts.sources import (
    StoredField,
    StoredSource,
    add_csv_source,
    find_source,
    read_dataset,
)
from artifacts.workspace import Workspace, open_workspace
from domain.trust import Actor


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Workspace]:
    opened = open_workspace(tmp_path / "workspace")
    yield opened
    opened.close()


@pytest.fixture
def dataset_id(workspace: Workspace) -> str:
    add_csv_source(
        workspace.engine,
        StoredSource(
            id="source-1",
            path=Path("/data/customers.csv"),
            delimiter=",",
            encoding="UTF-8",
            has_header=True,
            size_bytes=10,
            modified_at="2024-01-01T00:00:00+00:00",
            fields=(
                StoredField("id", "BIGINT", "integer"),
                StoredField("city", "VARCHAR", "string"),
            ),
        ),
    )
    return read_dataset(workspace.engine, "source-1").id


def test_workspace_settings_read_back(workspace: Workspace) -> None:
    # CFX-APP-first-slice/AC-35: Workspace.
    save_settings(workspace.engine, WorkspaceSettings("Sales", "local"))

    assert read_settings(workspace.engine) == WorkspaceSettings("Sales", "local")


def test_a_dataset_and_its_fields_read_back(
    workspace: Workspace, dataset_id: str
) -> None:
    # CFX-APP-first-slice/AC-35: Source, Dataset and Field.
    dataset = read_dataset(workspace.engine, "source-1")

    assert dataset.id == dataset_id
    assert dataset.source_id == "source-1"
    assert dataset.name == "customers.csv"
    assert [(f.name, f.physical_type, f.normalised_type) for f in dataset.fields] == [
        ("id", "BIGINT", "integer"),
        ("city", "VARCHAR", "string"),
    ]
    assert len({field.id for field in dataset.fields}) == 2
    source = find_source(workspace.engine, "source-1")
    assert source is not None
    assert source.kind == "csv"


def test_a_profile_run_reads_back(workspace: Workspace, dataset_id: str) -> None:
    # CFX-APP-first-slice/AC-35: ProfileRun.
    run_id = start_profile_run(
        workspace.engine,
        scope={"datasets": [dataset_id]},
        mode="sample",
        sample_size=10_000,
        strategy="reservoir",
        seed=42,
    )

    run = read_profile_run(workspace.engine, run_id)

    assert run.scope == {"datasets": [dataset_id]}
    assert run.mode == "sample"
    assert run.sample_size == 10_000
    assert run.strategy == "reservoir"
    assert run.seed == 42
    assert run.status == "running"
    assert run.started_at.startswith("20")
    assert run.ended_at is None


def test_an_observation_reads_back_with_how_it_was_measured(
    workspace: Workspace, dataset_id: str
) -> None:
    # CFX-APP-first-slice/AC-35: Observation, with the example record in Section 12.
    field = read_dataset(workspace.engine, "source-1").fields[0]
    run_id = start_profile_run(workspace.engine, scope={}, mode="sample")

    add_observations(
        workspace.engine,
        Actor.SYSTEM,
        run_id,
        [
            NewObservation(
                dataset_id=dataset_id,
                field_id=field.id,
                metric="uniqueness",
                value=0.998,
                scope="sample",
                method="distinct_count",
                precision="exact",
            )
        ],
    )

    observation = read_observations(workspace.engine, run_id)[0]
    assert observation.profile_run_id == run_id
    assert observation.dataset_id == dataset_id
    assert observation.field_id == field.id
    assert observation.metric == "uniqueness"
    assert observation.value == 0.998
    assert observation.scope == "sample"
    assert observation.method == "distinct_count"
    assert observation.precision == "exact"


def test_a_finding_reads_back_with_its_observations(
    workspace: Workspace, dataset_id: str
) -> None:
    # CFX-APP-first-slice/AC-35: Finding.
    run_id = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(
        workspace.engine,
        Actor.SYSTEM,
        run_id,
        [NewObservation(dataset_id, None, "row_count", 12, "full", "count", "exact")],
    )
    observation_id = read_observations(workspace.engine, run_id)[0].id

    finding_id = add_finding(
        workspace.engine,
        Actor.SYSTEM,
        NewFinding(
            profile_run_id=run_id,
            observation_ids=(observation_id,),
            interpretation="Only a dozen rows",
            why_it_matters="Too few to draw conclusions from",
            recommended_action="Check the export",
        ),
    )

    finding = read_finding(workspace.engine, finding_id)
    assert finding.observation_ids == (observation_id,)
    assert finding.interpretation == "Only a dozen rows"
    assert finding.why_it_matters == "Too few to draw conclusions from"
    assert finding.recommended_action == "Check the export"


def test_a_contract_reads_back(workspace: Workspace, dataset_id: str) -> None:
    # CFX-APP-first-slice/AC-35: Contract, which has no state of its own (ADR 0003 point 2).
    contract_id = add_contract(
        workspace.engine, dataset_id, purpose="Billing", limitations="EU only"
    )

    contract = read_contract(workspace.engine, contract_id)

    assert contract.dataset_id == dataset_id
    assert contract.purpose == "Billing"
    assert contract.limitations == "EU only"
    assert contract.version == 1


def test_a_dataset_has_at_most_one_contract(
    workspace: Workspace, dataset_id: str
) -> None:
    # ADR 0003 point 3: one contract covers one dataset.
    add_contract(workspace.engine, dataset_id, purpose="Billing", limitations="")

    with pytest.raises(ValueError, match="already has a contract"):
        add_contract(workspace.engine, dataset_id, purpose="Other", limitations="")


def test_the_installed_wheel_includes_the_domain_model(conflux_executable: str) -> None:
    # The running app doesn't import these yet, so nothing else would notice them missing.
    python = Path(conflux_executable).parent / "python"

    result = subprocess.run(
        [
            str(python),
            "-c",
            "import artifacts.profiles, artifacts.review, domain.trust",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
