import pytest
from pydantic import ValidationError

from fin_assistant.config import Settings

BASE = {"database_url": "postgresql://u:p@localhost:5435/db"}


def test_disk_budget_is_bounded() -> None:
    s = Settings(**BASE, log_max_mb=20, log_backup_count=4)
    budget = s.log_max_mb * 1024 * 1024 * (s.log_backup_count + 1)
    assert budget == 100 * 1024 * 1024


def test_zero_backups_still_capped() -> None:
    s = Settings(**BASE, log_max_mb=5, log_backup_count=0)
    assert s.log_backup_count == 0
    assert s.log_max_mb * (s.log_backup_count + 1) == 5


def test_zero_max_mb_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**BASE, log_max_mb=0)


def test_negative_backup_count_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**BASE, log_backup_count=-1)


def test_bad_log_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="log_level"):
        Settings(**BASE, log_level="LOUD")


def test_log_level_is_case_insensitive() -> None:
    assert Settings(**BASE, log_level="debug").log_level == "debug"
