============================================================
  Migration Archive - Integer PK to UUID
  Date: 2026-04-07
============================================================

This folder contains backup copies of one-time migration files
used to convert ALL model primary keys and foreign keys from
Integer (auto-increment) to UUID (PostgreSQL native).

Files:
------
  0001_int_pk_to_uuid.py
    - Alembic migration script that performed the conversion
    - Covers all 26 tables across core / just_right / nest_talk / trade_copilot
    - Handles physical FK constraints, logical FK columns, and the
      core_user_roles many-to-many association table
    - This migration is irreversible (downgrade raises NotImplementedError)

What was changed:
-----------------
  1. CoreModel base class (core/database.py)
     - id: Integer autoincrement  ->  UUID (uuid4 default)

  2. All model files
     - apps/just_right/models.py
     - apps/nest_talk/models.py
     - apps/trade_copilot/models.py
     - core/associations.py (user_roles table)

  3. All schema files (Pydantic)
     - id: int  ->  id: UUID
     - All FK reference fields: int -> UUID

  4. Alembic infrastructure (kept in project root for future use)
     - alembic.ini
     - alembic/env.py
     - alembic/script.py.mako
     - alembic/versions/  (migration scripts live here)

Note:
-----
  The original migration script still lives at:
    alembic/versions/0001_int_pk_to_uuid.py
  This copy is for reference only. Do NOT delete the original
  from alembic/versions/ -- Alembic needs it to track history.
