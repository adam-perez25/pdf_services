import json
import zipfile
from io import BytesIO

import pytest

from conftest import make_pdf, page_layout


def split(client, content: bytes, **data):
    return client.post("/api/split-zip", files={"file": ("informe.pdf", content)}, data=data)


def zip_contents(response) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_one_pdf_per_page_in_a_single_zip(client, sample_pdf):
    response = split(client, sample_pdf, mode="pages", base_name="parte")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="parte.zip"'
    files = zip_contents(response)
    assert list(files) == [f"parte-page-00{n}.pdf" for n in range(1, 5)]
    assert [page_layout(files[name]) for name in files] == [[(n * 100, 0)] for n in range(1, 5)]


def test_page_numbers_are_padded_so_files_sort_in_order(client):
    response = split(client, make_pdf(12), mode="pages", base_name="p")

    names = list(zip_contents(response))
    assert names[0] == "p-page-001.pdf"
    assert names[-1] == "p-page-012.pdf"
    assert names == sorted(names)


def test_ranges_use_the_given_names(client, sample_pdf):
    response = split(
        client,
        sample_pdf,
        mode="ranges",
        ranges="1-2, 4",
        names=json.dumps(["Capítulo uno", "anexo.pdf"]),
    )

    assert response.headers["content-disposition"] == 'attachment; filename="informe-split.zip"'
    files = zip_contents(response)
    # Accents and ñ are kept in the names.
    assert list(files) == ["Capítulo uno.pdf", "anexo.pdf"]
    assert page_layout(files["Capítulo uno.pdf"]) == [(100, 0), (200, 0)]
    assert page_layout(files["anexo.pdf"]) == [(400, 0)]


def test_duplicate_names_do_not_overwrite_each_other(client, sample_pdf):
    response = split(client, sample_pdf, mode="ranges", ranges="1, 2, 3", names=json.dumps(["a", "A", "a"]))

    assert list(zip_contents(response)) == ["a.pdf", "A-2.pdf", "a-3.pdf"]


def test_missing_names_get_a_default(client, sample_pdf):
    response = split(client, sample_pdf, mode="ranges", ranges="1, 3-4", names=json.dumps(["primero", None]))

    assert list(zip_contents(response)) == ["primero.pdf", "split-3-4.pdf"]


@pytest.mark.parametrize("names", [None, "null", ""])
def test_no_names_at_all_use_the_defaults(client, sample_pdf, names):
    data = {} if names is None else {"names": names}
    response = split(client, sample_pdf, mode="ranges", ranges="1, 3-4", **data)

    assert list(zip_contents(response)) == ["split-1-1.pdf", "split-3-4.pdf"]


def test_empty_or_dot_names_fall_back_to_the_range_default(client, sample_pdf):
    response = split(client, sample_pdf, mode="ranges", ranges="1, 2, 3-4", names=json.dumps(["", "...", "  "]))

    assert list(zip_contents(response)) == ["split-1-1.pdf", "split-2-2.pdf", "split-3-4.pdf"]


@pytest.mark.parametrize(
    "names",
    [
        json.dumps(["solo uno"]),  # fewer names than ranges
        json.dumps(["a", "b", "c"]),  # more names than ranges
        json.dumps({"a": 1}),
        json.dumps("a"),
        json.dumps(["a", 3]),
        json.dumps(["a", ["b"]]),
        "[" * 100_000 + "]" * 100_000,  # deeper than the JSON parser allows
    ],
)
def test_names_must_be_one_string_per_range(client, sample_pdf, documents_dir, names):
    response = split(client, sample_pdf, mode="ranges", ranges="1, 3-4", names=names)

    assert response.status_code == 400
    assert response.json()["detail"].startswith("Los nombres no son válidos")
    assert list(documents_dir.iterdir()) == []


def test_single_part_is_returned_as_a_pdf(client, sample_pdf):
    response = split(client, sample_pdf, mode="ranges", ranges="2-3", names=json.dumps(["medio"]))

    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="medio.pdf"'
    assert page_layout(response.content) == [(200, 0), (300, 0)]


def test_single_page_document_in_pages_mode_is_a_pdf(client):
    response = split(client, make_pdf(1), mode="pages", base_name="solo")

    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="solo-page-001.pdf"'


@pytest.mark.parametrize("ranges", ["", "1-9", "3-2", "0", "abc"])
def test_invalid_ranges_are_rejected(client, sample_pdf, ranges):
    response = split(client, sample_pdf, mode="ranges", ranges=ranges)

    assert response.status_code == 400


@pytest.mark.parametrize(
    "data",
    [{"mode": "everything"}, {"mode": "ranges", "ranges": "1", "names": "not json"}],
)
def test_invalid_options_are_rejected(client, sample_pdf, data):
    assert split(client, sample_pdf, **data).status_code == 400


def test_rejects_non_pdf_and_corrupt_files(client):
    assert client.post("/api/split-zip", files={"file": ("a.txt", b"x")}).status_code == 400
    assert client.post("/api/split-zip", files={"file": ("a.pdf", b"x")}).status_code == 400


def test_no_temporary_files_are_left(client, sample_pdf, documents_dir):
    split(client, sample_pdf, mode="pages")
    split(client, sample_pdf, mode="ranges", ranges="1-9")

    assert list(documents_dir.iterdir()) == []
