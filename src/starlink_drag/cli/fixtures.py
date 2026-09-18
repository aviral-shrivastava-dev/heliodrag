"""Materialise the offline fixture set: python -m starlink_drag.fixtures_cli"""

from .. import fixtures

if __name__ == "__main__":
    summary = fixtures.write_all()
    for key, value in summary.items():
        print(f"{key}: {value}")
