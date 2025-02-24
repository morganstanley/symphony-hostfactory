"""Common pytest for unit test"""


def pytest_collection_modifyitems(items) -> None:
    """Mark the test with the unit marker."""
    for item in items:
        if item.module and "unit" in str(item.module):
            item.add_marker("unit")
