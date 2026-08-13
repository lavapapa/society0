from collections.abc import MutableMapping, MutableSequence
from copy import deepcopy

import pytest

from society0.state_proxy import DictProxy, ListProxy


def _dict_proxy(target: dict) -> DictProxy:
    return DictProxy(target, lambda event: None, lambda: [])


def _list_proxy(target: list) -> ListProxy:
    return ListProxy(target, lambda event: None, lambda: [])


def test_dict_proxy_is_a_mutable_mapping() -> None:
    target = {"nested": {"value": 1}}
    proxy = _dict_proxy(target)

    assert isinstance(proxy, MutableMapping)
    proxy["added"] = 2
    assert target["added"] == 2
    assert isinstance(proxy["nested"], MutableMapping)


def test_dict_proxy_pop_distinguishes_omitted_default_from_explicit_none() -> None:
    target = {"present": None}
    proxy = _dict_proxy(target)

    assert proxy.pop("present") is None
    assert proxy.pop("missing", None) is None
    assert proxy.pop("also-missing", "fallback") == "fallback"
    with pytest.raises(KeyError, match="required"):
        proxy.pop("required")


def test_list_proxy_is_a_mutable_sequence() -> None:
    target = [{"value": 1}]
    proxy = _list_proxy(target)

    assert isinstance(proxy, MutableSequence)
    proxy.append(2)
    assert target == [{"value": 1}, 2]
    assert isinstance(proxy[0], MutableMapping)


def test_nested_proxy_is_reused_while_container_identity_is_unchanged() -> None:
    target = {"nested": {"rows": [{"value": 1}]}}
    proxy = _dict_proxy(target)

    nested = proxy["nested"]
    rows = nested["rows"]
    row = rows[0]

    assert proxy["nested"] is nested
    assert proxy["nested"]["rows"] is rows
    assert proxy["nested"]["rows"][0] is row


def test_nested_proxy_is_replaced_when_container_identity_changes() -> None:
    target = {"nested": {"value": 1}, "rows": [{"value": 1}]}
    proxy = _dict_proxy(target)
    nested = proxy["nested"]
    rows = proxy["rows"]
    row = rows[0]

    proxy["nested"] = {"value": 2}
    rows[0] = {"value": 2}

    assert proxy["nested"] is not nested
    assert proxy["nested"]["value"] == 2
    assert rows[0] is not row
    assert rows[0]["value"] == 2


def test_list_proxy_compares_like_a_normal_python_list() -> None:
    proxy = _list_proxy([{"id": "a"}, [1, 2]])

    assert proxy == [{"id": "a"}, [1, 2]]
    assert [{"id": "a"}, [1, 2]] == proxy
    assert proxy != [{"id": "b"}]


def test_deepcopy_detaches_nested_proxies_as_plain_collections() -> None:
    target = {
        "lot": {
            "tax_lineage": [{"source_tax_liability_id": "tax-1"}],
            "metadata": {"source": "transfer"},
        }
    }
    proxy = _dict_proxy(target)

    snapshot = deepcopy(dict(proxy["lot"]))

    assert type(snapshot) is dict
    assert type(snapshot["tax_lineage"]) is list
    assert type(snapshot["tax_lineage"][0]) is dict
    assert type(snapshot["metadata"]) is dict
    snapshot["tax_lineage"][0]["source_tax_liability_id"] = "changed"
    assert target["lot"]["tax_lineage"][0]["source_tax_liability_id"] == "tax-1"
