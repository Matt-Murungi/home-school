# scripts

## Purpose

Repo tooling: manifest runner, layout guard, secret scan, and the F01 tests.

## Test command

`python3 -m unittest discover -s scripts/tests -v` (run from the repo root), or `make test` from the repo root, which runs every package listed in `packages.json`.

## Status

Implemented: runs in `make test` as `root-tests`.
