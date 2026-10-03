from importlib import import_module

import pytest


@pytest.mark.parametrize("vector", [[], [0, 0, 0], [1, 2], [float("nan"), 1, 1]])
def test_invalid_query_vectors_fail_before_database_access(vector):
    retrieve = import_module("app.services.retrieval").retrieve
    with pytest.raises(ValueError):
        retrieve(
            None,
            question="parking",
            query_embedding=vector,
            context={},
            model="test",
            version="v1",
            dimension=3,
        )
