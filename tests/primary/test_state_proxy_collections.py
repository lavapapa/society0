from collections.abc import MutableMapping, MutableSequence

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


def test_list_proxy_is_a_mutable_sequence() -> None:
    target = [{"value": 1}]
    proxy = _list_proxy(target)

    assert isinstance(proxy, MutableSequence)
    proxy.append(2)
    assert target == [{"value": 1}, 2]
    assert isinstance(proxy[0], MutableMapping)
