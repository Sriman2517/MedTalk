from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS phones (
    phone_number TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patient_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number TEXT NOT NULL,
    name TEXT NOT NULL,
    age INTEGER NOT NULL,
    gender TEXT NOT NULL,
    preferred_language TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(phone_number) REFERENCES phones(phone_number)
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number TEXT NOT NULL,
    patient_profile_id INTEGER,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    language TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    active_case_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(phone_number) REFERENCES phones(phone_number),
    FOREIGN KEY(patient_profile_id) REFERENCES patient_profiles(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    sender_role TEXT NOT NULL,
    message_type TEXT NOT NULL,
    original_text TEXT,
    translated_text TEXT,
    audio_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS doctors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    specialty TEXT NOT NULL,
    available INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id INTEGER NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL,
    specialty TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(doctor_id) REFERENCES doctors(id)
);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    patient_profile_id INTEGER NOT NULL,
    summary_english TEXT NOT NULL,
    urgency TEXT NOT NULL,
    specialty_hint TEXT NOT NULL,
    status TEXT NOT NULL,
    assigned_doctor_id INTEGER,
    assigned_specialist_id INTEGER,
    referral_note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id),
    FOREIGN KEY(patient_profile_id) REFERENCES patient_profiles(id),
    FOREIGN KEY(assigned_doctor_id) REFERENCES doctors(id),
    FOREIGN KEY(assigned_specialist_id) REFERENCES doctors(id)
);

CREATE TABLE IF NOT EXISTS doctor_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    doctor_id INTEGER NOT NULL,
    english_reply TEXT NOT NULL,
    translated_reply TEXT NOT NULL,
    audio_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(case_id) REFERENCES cases(id),
    FOREIGN KEY(doctor_id) REFERENCES doctors(id)
);
"""


def get_connection(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(database_path) as connection:
        connection.executescript(SCHEMA)
        connection.commit()


@contextmanager
def transaction(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = get_connection(database_path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()
