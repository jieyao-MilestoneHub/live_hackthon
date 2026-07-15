"""``presigned_get`` Content-Disposition / Content-Type response overrides.

The download route signs ``attachment`` (save-to-disk); the preview route signs
``inline`` + ``video/mp4`` so a browser ``<video>`` streams the SAME object in
place. Presigning is client-side signing (no network), so the signed query
string carries the response-header overrides verbatim. Default (no kwargs) must
stay byte-for-byte the legacy behaviour — no override params at all.

``content_disposition()`` is a pure helper → table-driven logic/boundary/error
cases. The ``S3Storage`` cases assert the object-state of the emitted URL under
the moto-backed ``aws`` fixture.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

OUTPUT_BUCKET = "video-editor-output-test"
KEY = "tenant=demo/project=p/artifacts/artifact=a/final.mp4"


def _settings():
    # Use the app's real settings wiring (bound to the moto backend by the ``aws``
    # fixture) rather than hand-listing fields — keeps this test agnostic to the
    # exact Settings signature.
    from app.settings import get_settings

    return get_settings()


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# --- content_disposition() pure helper: logic / boundary / error ------------

@pytest.mark.parametrize(
    ("disposition", "filename", "expected"),
    [
        # inline never carries a filename (it's for in-page playback, not saving).
        ("inline", "clip.mp4", "inline"),
        ("inline", None, "inline"),
        # attachment quotes a plain filename (typical).
        ("attachment", "my clip.mp4", 'attachment; filename="my clip.mp4"'),
        # attachment without a filename stays bare (edge).
        ("attachment", None, "attachment"),
    ],
)
def test_content_disposition_builds_expected_header(disposition, filename, expected) -> None:
    from app.storage import content_disposition

    assert content_disposition(disposition, filename) == expected


def test_content_disposition_strips_header_injection_chars() -> None:
    from app.storage import content_disposition

    assert (
        content_disposition("attachment", 'e"vil\r\n/..\\x.mp4')
        == 'attachment; filename="evil..x.mp4"'
    )


def test_content_disposition_strips_c0_and_del_control_chars() -> None:
    from app.storage import content_disposition

    assert content_disposition("attachment", "a\x00b\x1f\x7fc.mp4") == 'attachment; filename="abc.mp4"'


def test_content_disposition_all_unsafe_filename_falls_back_to_download() -> None:
    from app.storage import content_disposition

    assert content_disposition("attachment", '"/\\\r\n') == 'attachment; filename="download"'


# --- S3Storage.presigned_get emitted-URL object-state (moto) ----------------

def test_default_presign_omits_disposition(aws) -> None:
    from app.storage import S3Storage

    url = S3Storage(_settings()).presigned_get(OUTPUT_BUCKET, KEY)

    assert "response-content-disposition" not in _query(url)


def test_default_presign_omits_content_type(aws) -> None:
    from app.storage import S3Storage

    url = S3Storage(_settings()).presigned_get(OUTPUT_BUCKET, KEY)

    assert "response-content-type" not in _query(url)


def test_inline_presign_sets_inline_disposition(aws) -> None:
    from app.storage import S3Storage

    url = S3Storage(_settings()).presigned_get(
        OUTPUT_BUCKET, KEY, disposition="inline", content_type="video/mp4"
    )

    assert _query(url)["response-content-disposition"] == "inline"


def test_inline_presign_forces_video_content_type(aws) -> None:
    from app.storage import S3Storage

    url = S3Storage(_settings()).presigned_get(
        OUTPUT_BUCKET, KEY, disposition="inline", content_type="video/mp4"
    )

    assert _query(url)["response-content-type"] == "video/mp4"


def test_attachment_presign_sets_filename_disposition(aws) -> None:
    from app.storage import S3Storage

    url = S3Storage(_settings()).presigned_get(
        OUTPUT_BUCKET, KEY, disposition="attachment", filename="p-pipeline.mp4"
    )

    assert _query(url)["response-content-disposition"] == 'attachment; filename="p-pipeline.mp4"'
