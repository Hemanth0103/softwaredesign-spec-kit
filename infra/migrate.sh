#!/bin/sh
# A migration updates database tables. Run it once per release, not automatically
# inside every API process. T006 supplies Alembic configuration; T009 adds tables.
set -eu
if [ ! -f /app/alembic.ini ] || [ ! -f /app/alembic/env.py ]; then
    echo 'Migrations are not available yet: implement T006 (Alembic) and T009 (tables) first.' >&2
    exit 1
fi
exec alembic -c /app/alembic.ini upgrade head
