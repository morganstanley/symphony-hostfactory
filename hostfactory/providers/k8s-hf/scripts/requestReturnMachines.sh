#!/usr/bin/env bash

inJson="$2"
hostfactory request-return-machines "$inJson" 2>> /tmp/hostfactory.log
