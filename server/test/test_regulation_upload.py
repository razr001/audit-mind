import asyncio
import io
import threading
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import BusinessException
from app.models.regulation import (
    KnowledgeCategory,
    KnowledgeVisibility,
    RegulationSourceType,
)
from app.schemas.regulation import RegulationUploadForm
from app.services.regulation_service import RegulationService, _calculate_stream_hash, settings
from test.pdf_fixtures import create_test_pdf


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class UploadRepository:
    def __init__(self, *, duplicate=None, save_error=None):
        self.duplicate = duplicate
        self.save_error = save_error
        self.lookups = []
        self.saved = []

    async def find_duplicate_by_content_hash(self, **kwargs):
        self.lookups.append(kwargs)
        return self.duplicate

    async def save(self, regulation):
        if self.save_error is not None:
            raise self.save_error
        self.saved.append(regulation)
        return regulation


class UploadStorage:
    def __init__(self):
        self.uploads = []
        self.removals = []

    async def upload(self, **kwargs):
        self.uploads.append(kwargs)
        return "regulations/test.pdf"

    async def remove(self, object_name):
        self.removals.append(object_name)


def source_file(content=None, filename="source.pdf"):
    if content is None:
        content = create_test_pdf()
    return UploadFile(filename=filename, file=io.BytesIO(content))


def upload_form(**overrides):
    values = {
        "title": "Data Security Law",
        "source_type": RegulationSourceType.LAW,
        "visibility": KnowledgeVisibility.SHARED,
        "jurisdiction": "CN",
    }
    values.update(overrides)
    return RegulationUploadForm(**values)


def run_upload(*, file=None, form=None, repository=None, storage=None, user_id=None):
    repository = repository or UploadRepository()
    storage = storage or UploadStorage()
    user_id = user_id or uuid4()
    service = RegulationService(uow=FakeUnitOfWork(), repository=repository, storage=storage)
    result = asyncio.run(
        service.upload(file=file or source_file(), form=form or upload_form(), user_id=user_id)
    )
    return result, repository, storage, user_id


def test_real_upload_service_validates_attributes_and_derives_server_owned_fields():
    result, repository, storage, user_id = run_upload()
    assert result.category == KnowledgeCategory.PUBLIC_KNOWLEDGE
    assert result.uploaded_by == user_id
    assert result.original_filename == "source.pdf"
    assert result.file_size == len(create_test_pdf())
    assert len(result.content_hash) == 64
    assert repository.saved == [result]
    assert len(storage.uploads) == 1
    assert repository.lookups[0]["visibility"] == KnowledgeVisibility.SHARED
    assert repository.lookups[0]["user_id"] == user_id


def test_large_upload_hashing_runs_outside_event_loop_thread():
    caller_thread = threading.get_ident()
    hashing_threads = []

    def observe_hashing(stream):
        hashing_threads.append(threading.get_ident())
        return _calculate_stream_hash(stream)

    with patch(
        "app.services.regulation_service._calculate_stream_hash",
        side_effect=observe_hashing,
    ):
        run_upload()

    assert hashing_threads
    assert hashing_threads[0] != caller_thread


def test_real_upload_service_rejects_empty_fake_oversized_and_unsafe_names_before_storage():
    cases = [source_file(b""), source_file(b"not-pdf"), source_file(filename="unsafe\nname.pdf")]
    for target in cases:
        storage = UploadStorage()
        try:
            run_upload(file=target, storage=storage)
        except BusinessException:
            pass
        else:
            raise AssertionError("unsafe file was accepted")
        assert storage.uploads == []

    storage = UploadStorage()
    with patch.object(settings, "REGULATION_MAX_FILE_SIZE", 5):
        try:
            run_upload(file=source_file(), storage=storage)
        except BusinessException:
            pass
        else:
            raise AssertionError("oversized file was accepted")
    assert storage.uploads == []


def test_real_upload_service_applies_visibility_aware_duplicate_lookup():
    user_id = uuid4()
    repository = UploadRepository(duplicate=SimpleNamespace(id=uuid4()))
    storage = UploadStorage()
    for visibility in (KnowledgeVisibility.SHARED, KnowledgeVisibility.PRIVATE):
        try:
            run_upload(
                form=upload_form(
                    source_type=RegulationSourceType.INTERNAL_POLICY, visibility=visibility
                ),
                repository=repository,
                storage=storage,
                user_id=user_id,
            )
        except BusinessException:
            pass
        else:
            raise AssertionError("duplicate source was accepted")
    assert [lookup["visibility"] for lookup in repository.lookups] == [
        KnowledgeVisibility.SHARED,
        KnowledgeVisibility.PRIVATE,
    ]
    assert all(lookup["user_id"] == user_id for lookup in repository.lookups)
    assert storage.uploads == []


def test_real_upload_service_keeps_object_after_concurrent_unique_conflict():
    error = IntegrityError("insert", {}, RuntimeError("unique conflict"))
    storage = UploadStorage()
    try:
        run_upload(repository=UploadRepository(save_error=error), storage=storage)
    except BusinessException:
        pass
    else:
        raise AssertionError("concurrent duplicate was accepted")
    assert storage.removals == []


def test_real_upload_service_keeps_object_after_save_failure():
    storage = UploadStorage()
    failure = RuntimeError("database unavailable")
    try:
        run_upload(repository=UploadRepository(save_error=failure), storage=storage)
    except RuntimeError as exc:
        assert exc is failure
    else:
        raise AssertionError("save failure was swallowed")
    assert storage.removals == []
