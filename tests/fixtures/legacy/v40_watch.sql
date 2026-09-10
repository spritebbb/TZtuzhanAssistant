PRAGMA user_version = 40;

CREATE TABLE watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    url TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    interval_minutes INTEGER NOT NULL DEFAULT 360,
    last_checked_at TEXT,
    last_hash TEXT NOT NULL DEFAULT '',
    last_changed_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, url)
);

INSERT INTO watches (
    user_id, url, label, interval_minutes, last_checked_at, last_hash,
    last_changed_at, status, created_at, updated_at
) VALUES (
    'fixture-user', 'https://example.invalid/status', 'fixture', 360,
    '2026-01-01T00:00:00', 'fixture-hash', '2026-01-01T00:00:00',
    'active', '2026-01-01T00:00:00', '2026-01-01T00:00:00'
);
