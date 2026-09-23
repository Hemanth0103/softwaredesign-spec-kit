-- PostgreSQL is the database; pgvector adds the ability to store/search vectors
-- (lists of numbers representing text). Installing it does not import any data.
-- Docker runs this file only when creating a NEW, empty database volume.
-- IF NOT EXISTS makes it safe to run manually on an existing database too.
CREATE EXTENSION IF NOT EXISTS vector;
