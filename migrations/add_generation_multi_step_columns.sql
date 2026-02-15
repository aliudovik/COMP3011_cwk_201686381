-- Migration: Add multi-step generation flow columns to generations table
-- These are all nullable so existing rows remain valid.

ALTER TABLE generations ADD COLUMN IF NOT EXISTS mood_intensity FLOAT;
ALTER TABLE generations ADD COLUMN IF NOT EXISTS activity TEXT;
ALTER TABLE generations ADD COLUMN IF NOT EXISTS song_reference TEXT;
ALTER TABLE generations ADD COLUMN IF NOT EXISTS genre TEXT;
ALTER TABLE generations ADD COLUMN IF NOT EXISTS bpm INTEGER;
