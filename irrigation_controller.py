from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SensorReadings:
    humidity_percent: float
    light_lux: float
    temperature_celsius: float


@dataclass(frozen=True)
class IrrigationConfig:
    min_humidity_percent: float = 35.0
    max_light_lux: float = 300.0
    min_temperature_celsius: float = 25.0
    stop_humidity_percent: float = 55.0
    max_irrigation_seconds: int = 120
    check_interval_seconds: int = 5


class SmartPlug:
    def turn_on(self) -> None:
        print("[SmartPlug] Corrente attivata")

    def turn_off(self) -> None:
        print("[SmartPlug] Corrente interrotta")


class ElectroValve:
    def open(self) -> None:
        print("[ElectroValve] Valvola aperta: irrigazione avviata")

    def close(self) -> None:
        print("[ElectroValve] Valvola chiusa: irrigazione fermata")


class SensorReader:
    def read(self) -> SensorReadings:
        # Sostituisci questa parte con la lettura reale dei tuoi sensori.
        return SensorReadings(
            humidity_percent=30.0,
            light_lux=120.0,
            temperature_celsius=28.0,
        )


class IrrigationController:
    def __init__(
        self,
        sensor_reader: SensorReader,
        smart_plug: SmartPlug,
        electro_valve: ElectroValve,
        config: IrrigationConfig,
    ) -> None:
        self.sensor_reader = sensor_reader
        self.smart_plug = smart_plug
        self.electro_valve = electro_valve
        self.config = config
        self.is_irrigating = False

    def should_start_irrigation(self, readings: SensorReadings) -> bool:
        return (
            readings.humidity_percent < self.config.min_humidity_percent
            and readings.light_lux < self.config.max_light_lux
            and readings.temperature_celsius > self.config.min_temperature_celsius
        )

    def should_stop_irrigation(self, readings: SensorReadings, elapsed_seconds: float) -> bool:
        return (
            readings.humidity_percent >= self.config.stop_humidity_percent
            or elapsed_seconds >= self.config.max_irrigation_seconds
        )

    def start_irrigation(self) -> None:
        if self.is_irrigating:
            return
        self.smart_plug.turn_on()
        self.electro_valve.open()
        self.is_irrigating = True

    def stop_irrigation(self) -> None:
        if not self.is_irrigating:
            return
        self.electro_valve.close()
        self.smart_plug.turn_off()
        self.is_irrigating = False

    def run_once(self) -> None:
        readings = self.sensor_reader.read()
        print(f"[Sensors] {readings}")

        if self.should_start_irrigation(readings):
            self.start_irrigation()
            self.monitor_until_stop()
        else:
            print("[Controller] Condizioni non sufficienti: irrigazione non avviata")

    def monitor_until_stop(self) -> None:
        started_at = time.monotonic()

        try:
            while True:
                time.sleep(self.config.check_interval_seconds)
                readings = self.sensor_reader.read()
                elapsed = time.monotonic() - started_at
                print(f"[Monitor] elapsed={elapsed:.1f}s readings={readings}")

                if self.should_stop_irrigation(readings, elapsed):
                    self.stop_irrigation()
                    break
        except KeyboardInterrupt:
            self.stop_irrigation()
            raise


def main() -> None:
    controller = IrrigationController(
        sensor_reader=SensorReader(),
        smart_plug=SmartPlug(),
        electro_valve=ElectroValve(),
        config=IrrigationConfig(
            min_humidity_percent=35.0,
            max_light_lux=300.0,
            min_temperature_celsius=25.0,
            stop_humidity_percent=55.0,
            max_irrigation_seconds=120,
            check_interval_seconds=5,
        ),
    )
    controller.run_once()


if __name__ == "__main__":
    main()
