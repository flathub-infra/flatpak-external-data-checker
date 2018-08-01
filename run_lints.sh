#!/bin/bash

pep8 --max-line-length=100 src/
pyflakes3 src/
