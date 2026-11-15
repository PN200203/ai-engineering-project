#!/bin/sh
(cd agentsquare && python3 -m unittest discover tests) && (cd hardwaresage && python3 -m unittest discover tests)
