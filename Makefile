#!/bin/bash

SRC_BASENAME=blah

all: a.out

clean:
	rm -f "${SRC_BASENAME}".o a.out

.PHONY: all clean

a.out: basicc.py blah.bas
	./basicc.py ${BASICCFLAGS} "${SRC_BASENAME}".bas
