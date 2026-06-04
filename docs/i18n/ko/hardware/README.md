# 하드웨어 및 주변 장치 문서

보드 연동, 펌웨어 플로우, 주변 장치 아키텍처를 다룹니다.

Revka의 하드웨어 서브시스템은 `Peripheral` 트레이트를 통해 마이크로컨트롤러와 주변 장치를 직접 제어합니다. 각 보드는 GPIO·ADC·센서 작업용 도구를 노출하며, 에이전트가 STM32 Nucleo, Raspberry Pi, ESP32 같은 보드와 직접 상호작용할 수 있게 해 줍니다. 전체 아키텍처는 [hardware-peripherals-design.md](hardware-peripherals-design.md) *(영문)* 를 참고하세요.

## 진입점

- 아키텍처와 주변 장치 모델: [hardware-peripherals-design.md](hardware-peripherals-design.md) *(영문)*
- 새 보드/도구 추가하기: [../contributing/adding-boards-and-tools.md](../contributing/adding-boards-and-tools.md) *(영문)*
- Nucleo 셋업: [nucleo-setup.md](nucleo-setup.md) *(영문)*
- Arduino Uno R4 WiFi 셋업: [arduino-uno-q-setup.md](arduino-uno-q-setup.md) *(영문)*

## 데이터시트

- 데이터시트 색인: [datasheets](../../../hardware/datasheets) *(영문)*
- STM32 Nucleo-F401RE: [datasheets/nucleo-f401re.md](../../../hardware/datasheets/nucleo-f401re.md) *(영문)*
- Arduino Uno: [datasheets/arduino-uno.md](../../../hardware/datasheets/arduino-uno.md) *(영문)*
- ESP32: [datasheets/esp32.md](../../../hardware/datasheets/esp32.md) *(영문)*
