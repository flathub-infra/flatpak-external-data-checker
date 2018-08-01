#!/bin/bash

PYTHONPATH=$PYTHONPATH:$PWD/src python3 tests/test_checker.py $@
