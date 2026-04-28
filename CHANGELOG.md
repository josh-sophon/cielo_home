# Changelog

## Unreleased

- Keep CT01 `previousMode` and local preferences coherent before sending thermostat action strings.
- Update `sync_ac_state` climate service state after CT01 commands so Home Assistant can report the immediate command result before cloud readback settles.

## 1.0.0

- First version support for Cielo Home
