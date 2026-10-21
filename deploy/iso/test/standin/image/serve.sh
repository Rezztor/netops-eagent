#!/bin/sh
# Answer one HTTP request.
read -r _ path _
case "$path" in
/api/health) type=application/json; body='{"status": "ok", "version": "stand-in"}' ;;
*) type=text/html; body='<!doctype html><title>Rezztor EAgent stand-in</title><div id="root">STAND-IN image, not the product</div>' ;;
esac
printf 'HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %s\r\nConnection: close\r\n\r\n%s' "$type" "${#body}" "$body"
