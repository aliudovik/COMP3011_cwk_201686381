-- Migration: Add is_favourite column to generations table
-- Nullable=false with default so existing rows get FALSE automatically.

ALTER TABLE generations ADD COLUMN IF NOT EXISTS is_favourite BOOLEAN NOT NULL DEFAULT FALSE;
