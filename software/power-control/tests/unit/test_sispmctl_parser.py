from pathlib import Path

from humanoid_power.adapters.sispmctl import SispmctlAdapter
from humanoid_power.domain.enums import OutletState, RepeatMode

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sispmctl"


class StubSispmctlAdapter(SispmctlAdapter):
    def __init__(self, output: str) -> None:
        super().__init__(binary_path="/bin/true")
        self.output = output

    def _run(self, *arguments: str, write: bool = False):  # type: ignore[no-untyped-def]
        from humanoid_power.adapters.sispmctl import _RunResult

        return _RunResult(True, stdout=self.output, duration_ms=1.0)


def test_scan_parses_single_controller() -> None:
    output = (FIXTURES / "scan_one.txt").read_text()
    result = StubSispmctlAdapter(output).scan_controllers()

    assert result.ok
    assert result.value is not None
    assert result.value.count == 1


def test_scan_parses_multiline_controller_output() -> None:
    output = (FIXTURES / "scan_one_multiline.txt").read_text()
    result = StubSispmctlAdapter(output).scan_controllers()

    assert result.ok
    assert result.value is not None
    assert result.value.count == 1
    assert "4-socket SiS-PM" in result.value.description
    assert "01:ff:ff:ff:ff" in result.value.description


def test_state_parser_accepts_only_zero_or_one() -> None:
    assert StubSispmctlAdapter("1\n").read_outlet_state(1).value is OutletState.ON
    assert StubSispmctlAdapter("0\n").read_outlet_state(1).value is OutletState.OFF

    invalid = StubSispmctlAdapter("enabled\n").read_outlet_state(1)

    assert not invalid.ok
    assert invalid.error_code == "UNEXPECTED_OUTPUT"


def test_schedule_parser_reads_events_and_loop() -> None:
    output = (FIXTURES / "schedule_daily.txt").read_text()
    result = StubSispmctlAdapter(output).read_schedule(1)

    assert result.ok
    assert result.value is not None
    assert len(result.value.events) == 2
    assert result.value.repeat_mode is RepeatMode.DAILY
    assert result.value.loop_minutes == 1440
