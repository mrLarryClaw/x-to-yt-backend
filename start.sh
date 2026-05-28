#!/bin/bash
set -e
cd /app
exec uvicorn src.main:app --host 0.0.0.0 --port $PORT
