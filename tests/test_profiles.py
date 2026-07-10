import pytest
import requests
from conftest import PROFILE_SOURCES, unique_name


@pytest.mark.parametrize("category", ["printer", "process", "filament"])
def test_upload_returns_201(api, category):
    name = unique_name()
    info = PROFILE_SOURCES[category]
    with open(info["path"], "rb") as f:
        r = requests.post(
            f"{api}/api/profiles/{category}",
            files={"file": (info["path"].name, f, "application/json")},
            data={"name": name},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == name
    assert body["category"] == category
    assert body["size"] > 0
    requests.delete(f"{api}/api/profiles/{category}/{name}")


@pytest.mark.parametrize("category", ["printer", "process", "filament"])
def test_upload_duplicate_returns_409(api, category):
    name = unique_name()
    info = PROFILE_SOURCES[category]
    for _ in range(2):
        with open(info["path"], "rb") as f:
            r = requests.post(
                f"{api}/api/profiles/{category}",
                files={"file": (info["path"].name, f, "application/json")},
                data={"name": name},
            )
    assert r.status_code == 409
    requests.delete(f"{api}/api/profiles/{category}/{name}")


def test_upload_non_json_returns_400(api):
    r = requests.post(
        f"{api}/api/profiles/printer",
        files={"file": ("bad.json", b"not json!!!", "application/json")},
    )
    assert r.status_code == 400


def test_upload_invalid_category_returns_400(api):
    r = requests.post(
        f"{api}/api/profiles/nozzle",
        files={"file": ("x.json", b"{}", "application/json")},
    )
    assert r.status_code == 400


@pytest.mark.parametrize("category", ["printer", "process", "filament"])
def test_list_includes_uploaded_profile(api, core_profiles, category):
    r = requests.get(f"{api}/api/profiles/{category}")
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == category
    names = [p["name"] for p in body["profiles"]]
    assert core_profiles[category] in names


def test_get_profile_returns_json(api, core_profiles):
    r = requests.get(f"{api}/api/profiles/filament/{core_profiles['filament']}")
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]
    body = r.json()
    assert "name" in body


def test_get_missing_profile_returns_404(api):
    r = requests.get(f"{api}/api/profiles/printer/does-not-exist-xyz")
    assert r.status_code == 404


def test_replace_profile(api):
    name = unique_name()
    info = PROFILE_SOURCES["filament"]
    with open(info["path"], "rb") as f:
        requests.post(
            f"{api}/api/profiles/filament",
            files={"file": (info["path"].name, f, "application/json")},
            data={"name": name},
        )
    with open(info["path"], "rb") as f:
        r = requests.put(
            f"{api}/api/profiles/filament/{name}",
            files={"file": (info["path"].name, f, "application/json")},
        )
    assert r.status_code == 200
    assert r.json()["name"] == name
    requests.delete(f"{api}/api/profiles/filament/{name}")


def test_rename_profile(api):
    old_name = unique_name()
    new_name = unique_name()
    info = PROFILE_SOURCES["process"]
    with open(info["path"], "rb") as f:
        requests.post(
            f"{api}/api/profiles/process",
            files={"file": (info["path"].name, f, "application/json")},
            data={"name": old_name},
        )
    r = requests.patch(
        f"{api}/api/profiles/process/{old_name}",
        json={"new_name": new_name},
    )
    assert r.status_code == 200
    assert r.json()["name"] == new_name
    assert requests.get(f"{api}/api/profiles/process/{old_name}").status_code == 404
    assert requests.get(f"{api}/api/profiles/process/{new_name}").status_code == 200
    requests.delete(f"{api}/api/profiles/process/{new_name}")


def test_rename_to_existing_returns_409(api):
    name_a = unique_name()
    name_b = unique_name()
    info = PROFILE_SOURCES["printer"]
    for name in (name_a, name_b):
        with open(info["path"], "rb") as f:
            requests.post(
                f"{api}/api/profiles/printer",
                files={"file": (info["path"].name, f, "application/json")},
                data={"name": name},
            )
    r = requests.patch(
        f"{api}/api/profiles/printer/{name_a}",
        json={"new_name": name_b},
    )
    assert r.status_code == 409
    requests.delete(f"{api}/api/profiles/printer/{name_a}")
    requests.delete(f"{api}/api/profiles/printer/{name_b}")


def test_delete_profile(api):
    name = unique_name()
    info = PROFILE_SOURCES["printer"]
    with open(info["path"], "rb") as f:
        requests.post(
            f"{api}/api/profiles/printer",
            files={"file": (info["path"].name, f, "application/json")},
            data={"name": name},
        )
    r = requests.delete(f"{api}/api/profiles/printer/{name}")
    assert r.status_code == 200
    assert requests.get(f"{api}/api/profiles/printer/{name}").status_code == 404


def test_delete_missing_returns_404(api):
    r = requests.delete(f"{api}/api/profiles/printer/does-not-exist-xyz")
    assert r.status_code == 404
