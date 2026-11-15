import socket

import pytest

from litnav.init import init
from litnav.library import Library


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never touch the network; anything that tries fails loudly."""
    def guard(*a, **k):
        raise RuntimeError("network access in a test")
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture
def lib(tmp_path):
    root = init(tmp_path / "lib", git=False, log=lambda *a: None)
    return Library(root)


BIB = """@article{smith2021cold,
    title = {Cold pools and the propagation of {MCS}s},
    author = {Smith, Jane A. and Jones, B.},
    journal = {Journal of the Atmospheric Sciences},
    volume = {78},
    pages = {123--145},
    year = {2021},
    doi = {10.1175/JAS-D-20-0001.1}
}
"""
