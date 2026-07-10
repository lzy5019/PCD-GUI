import unittest
from unittest.mock import patch

from main import SignalGeneratorClient


class FakeInstrument:
    def __init__(self) -> None:
        self.closed = False
        self.timeout = None
        self.read_termination = None
        self.write_termination = None

    def query(self, command: str) -> str:
        self.last_query = command
        return "RIGOL TECHNOLOGIES,DG1062Z,TEST,1.0\n"

    def close(self) -> None:
        self.closed = True


class FakeResourceManager:
    def __init__(self) -> None:
        self.closed = False
        self.instrument = FakeInstrument()

    def open_resource(self, resource_name: str) -> FakeInstrument:
        self.resource_name = resource_name
        return self.instrument

    def close(self) -> None:
        self.closed = True


class SignalGeneratorClientTests(unittest.TestCase):
    def test_connect_discards_a_stale_resource_manager(self) -> None:
        client = SignalGeneratorClient()
        stale_manager = FakeResourceManager()
        fresh_manager = FakeResourceManager()
        client._resource_manager = stale_manager

        with patch.object(client, "_get_resource_manager", return_value=fresh_manager):
            identity = client.connect("USB0::TEST::INSTR", 2_000)

        self.assertTrue(stale_manager.closed)
        self.assertEqual(fresh_manager.resource_name, "USB0::TEST::INSTR")
        self.assertEqual(identity, "RIGOL TECHNOLOGIES,DG1062Z,TEST,1.0")
        self.assertEqual(fresh_manager.instrument.timeout, 2_000)


if __name__ == "__main__":
    unittest.main()
