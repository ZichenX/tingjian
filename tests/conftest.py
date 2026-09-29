import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings
from app.security import hash_code
from tests.fakes import FakeEngine

CODE='test-only-code-123'
@pytest.fixture(scope='session')
def password_hash(): return hash_code(CODE)

@pytest.fixture
def settings(password_hash):
    return Settings(origin='http://localhost:8000', secret='test-signing-secret-'*4, code_hash=password_hash,
                    max_upload_mb=1, max_upload_seconds=10, session_seconds=60)

@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, FakeEngine), base_url=settings.origin) as client:
        yield client

@pytest.fixture
def signed(client, settings):
    response = client.post('/api/login', headers={'Origin':settings.origin}, json={'code':CODE})
    assert response.status_code == 200
    csrf = client.get('/api/session').json()['csrf']
    return client, {'Origin':settings.origin,'X-CSRF-Token':csrf}
