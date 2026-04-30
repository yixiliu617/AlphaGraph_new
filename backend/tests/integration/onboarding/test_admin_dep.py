import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app.api.auth_deps import require_admin


def test_require_admin_blocks_non_admin():
    """A user with admin_role='user' is rejected with 403 by require_admin."""
    from unittest.mock import MagicMock
    from backend.app.api.auth_deps import require_admin
    from fastapi import HTTPException

    fake_user = MagicMock(admin_role="user", id="abc")
    with pytest.raises(HTTPException) as exc:
        require_admin(current_user=fake_user)
    assert exc.value.status_code == 403


def test_require_admin_allows_admin():
    from unittest.mock import MagicMock
    fake_user = MagicMock(admin_role="admin", id="abc")
    result = require_admin(current_user=fake_user)
    assert result is fake_user
