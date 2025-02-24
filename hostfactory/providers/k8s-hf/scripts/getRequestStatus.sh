#!/usr/bin/env bash

inJson="$2"
hostfactory get-request-status "$inJson" 2>> /tmp/hostfactory.log
