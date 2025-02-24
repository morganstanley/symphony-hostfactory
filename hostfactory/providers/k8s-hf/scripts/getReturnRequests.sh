#!/usr/bin/env bash

inJson="$2"
hostfactory get-return-requests "$inJson" 2>> /tmp/hostfactory.log