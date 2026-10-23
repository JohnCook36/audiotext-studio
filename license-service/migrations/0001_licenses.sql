CREATE TABLE IF NOT EXISTS licenses (
  id TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL DEFAULT '',
  term_days INTEGER,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
  device_hash TEXT,
  activated_at INTEGER,
  expires_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_licenses_status ON licenses(status);
CREATE INDEX IF NOT EXISTS idx_licenses_device ON licenses(device_hash);
