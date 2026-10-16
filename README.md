# ot-energy-monitoring

A small generation fleet simulated in containers (wind turbines, solar inverters, a battery
and a substation) serving Modbus registers the way real equipment does, monitored with
Prometheus and Grafana across separate OT, DMZ and IT networks. Everything is simulated and
generic.

## Quick start

    cp .env.example .env
    docker compose run --rm unit-tests
    docker compose up -d
    docker compose run --rm ot-workstation dump wt-01

## Layout

    fleet/       device simulator, register map, Modbus TCP code (standard library only)
    scenarios/   files that inject conditions into the simulated world (faults, grid events)
    tests/       unit tests

## Status

Work in progress