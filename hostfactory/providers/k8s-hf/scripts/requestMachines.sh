#!/usr/bin/env bash

inJson="$2"
hostfactory request-machines "$inJson" 2>> /tmp/hostfactory.log
