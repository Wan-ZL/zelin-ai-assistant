#!/bin/bash
# Delete screenpipe raw media older than 1 hour.
# Keeps only OCR text + audio transcriptions in db.sqlite.
find "$HOME/.screenpipe/data" -name "*.jpg" -mmin +60 -delete 2>/dev/null
find "$HOME/.screenpipe/data" -name "*.mp4" -mmin +60 -delete 2>/dev/null
