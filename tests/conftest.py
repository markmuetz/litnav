import socket

import pytest

from litnav.init import init
from litnav.library import Library


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never touch the network; anything that tries fails loudly.
    Loopback stays open: asyncio's event loop on Windows uses a local socket
    pair."""
    real_cc, real_connect = socket.create_connection, socket.socket.connect

    def local(address):
        return isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost")

    def create_connection(address, *a, **k):
        if not local(address):
            raise RuntimeError("network access in a test")
        return real_cc(address, *a, **k)

    def connect(self, address):
        if not local(address):
            raise RuntimeError("network access in a test")
        return real_connect(self, address)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket.socket, "connect", connect)


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
