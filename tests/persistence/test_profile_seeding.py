from pathlib import Path

import pytest

from auction_watch.persistence import (
    Database,
    ProfileRepository,
    SystemProfileDeleteError,
    SystemProfileImmutableError,
    upgrade_head,
)
from auction_watch.profiles.seed import consoles_profile


@pytest.fixture
def repository(tmp_path: Path) -> ProfileRepository:
    database = Database.open(tmp_path)
    upgrade_head(tmp_path, database.engine)
    yield ProfileRepository(database)
    database.dispose()


def test_consolas_seed_is_idempotent_and_preserves_pause(repository: ProfileRepository) -> None:
    first = repository.seed_system_profile(consoles_profile())
    assert first.profile.kind == "system"
    assert first.profile.locked is True
    assert repository.seed_system_profile(consoles_profile()).revision == 1

    paused = first.profile.model_copy(update={"enabled": False})
    paused_result = repository.replace(paused, expected_revision=1)
    upgraded = consoles_profile().model_copy(
        update={"seed_version": 2, "keywords_any": ("consola", "hardware")}
    )
    result = repository.seed_system_profile(upgraded)
    assert result.revision == paused_result.revision + 1
    assert result.profile.enabled is False
    assert result.profile.seed_version == 2


def test_system_profile_cannot_be_deleted_or_change_identity(repository: ProfileRepository) -> None:
    created = repository.seed_system_profile(consoles_profile())
    with pytest.raises(SystemProfileDeleteError):
        repository.delete("consolas", expected_revision=created.revision)
    with pytest.raises(SystemProfileImmutableError):
        repository.replace(
            consoles_profile().model_copy(update={"kind": "user", "locked": False}),
            expected_revision=created.revision,
        )
    with pytest.raises(SystemProfileImmutableError):
        repository.replace(
            consoles_profile().model_copy(update={"id": "otro-slug"}),
            expected_revision=created.revision,
        )
    with pytest.raises(SystemProfileImmutableError):
        repository.replace(
            consoles_profile().model_copy(update={"keywords_any": ("solo-otro",)}),
            expected_revision=created.revision,
        )


def test_system_profile_can_be_cloned_as_editable_user_profile(
    repository: ProfileRepository,
) -> None:
    repository.seed_system_profile(consoles_profile())
    cloned = repository.clone("consolas", "mis-consolas", "Mis consolas")
    assert cloned.profile.id == "mis-consolas"
    assert cloned.profile.name == "Mis consolas"
    assert cloned.profile.kind == "user"
    assert cloned.profile.locked is False
    assert cloned.profile.seed_key is None
    assert cloned.profile.source_ids == consoles_profile().source_ids
    edited_clone = cloned.profile.model_copy(update={"keywords_any": ("solo-mis-regla",)})
    repository.replace(edited_clone, expected_revision=cloned.revision)
    original = repository.get("consolas")
    assert original is not None
    assert original.profile.keywords_any == consoles_profile().keywords_any
    repository.delete(cloned.profile.id, expected_revision=2)


def test_seed_does_not_modify_unrelated_user_profile(repository: ProfileRepository) -> None:
    custom = consoles_profile().model_copy(
        update={
            "id": "mi-perfil",
            "name": "Mi perfil",
            "kind": "user",
            "locked": False,
            "seed_key": None,
            "seed_version": 0,
            "keywords_any": ("libro",),
            "source_ids": ("remotes",),
        }
    )
    repository.create(custom)
    repository.seed_system_profile(consoles_profile())

    stored = repository.get("mi-perfil")
    assert stored is not None
    assert stored.profile == custom
