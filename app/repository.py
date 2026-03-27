from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db import transaction
from app.triage import dump_context, load_context


class Repository:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def seed_doctors(self) -> None:
        with transaction(self.database_path) as connection:
            count = connection.execute("SELECT COUNT(*) AS count FROM doctors").fetchone()["count"]
            if count:
                return
            doctors = [
                ("Dr. Meera Rao", "gp", "general_medicine", 1),
                ("Dr. Arjun Patel", "gp", "general_medicine", 1),
                ("Dr. Kavya Reddy", "specialist", "cardio", 1),
                ("Dr. Sanjay Iyer", "specialist", "pulmonology", 1),
                ("Dr. Nithya Krishnan", "specialist", "dermatology", 1),
                ("Dr. Vivek Sharma", "specialist", "gastroenterology", 1),
            ]
            connection.executemany(
                "INSERT INTO doctors(name, role, specialty, available) VALUES (?, ?, ?, ?)",
                doctors,
            )

    def create_user(
        self,
        *,
        full_name: str,
        email: str,
        password_hash: str,
        password_salt: str,
        role: str,
        specialty: str,
    ) -> int:
        with transaction(self.database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO doctors(name, role, specialty, available) VALUES (?, ?, ?, 1)",
                (full_name, role, specialty),
            )
            doctor_id = int(cursor.lastrowid)
            user_cursor = connection.execute(
                """
                INSERT INTO users(doctor_id, full_name, email, password_hash, password_salt, role, specialty, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (doctor_id, full_name, email.lower(), password_hash, password_salt, role, specialty),
            )
            return int(user_cursor.lastrowid)

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with transaction(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT id, doctor_id, full_name, email, password_hash, password_salt, role, specialty, is_active, created_at
                FROM users
                WHERE email = ?
                """,
                (email.lower(),),
            ).fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with transaction(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT id, doctor_id, full_name, email, password_hash, password_salt, role, specialty, is_active, created_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            return dict(row) if row else None

    def ensure_phone(self, phone_number: str) -> None:
        with transaction(self.database_path) as connection:
            connection.execute("INSERT OR IGNORE INTO phones(phone_number) VALUES (?)", (phone_number,))

    def get_profiles(self, phone_number: str) -> list[dict[str, Any]]:
        with transaction(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT id, phone_number, name, age, gender, preferred_language, created_at
                FROM patient_profiles
                WHERE phone_number = ?
                ORDER BY id
                """,
                (phone_number,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_profile(self, phone_number: str, name: str, age: int, gender: str, language: str) -> int:
        with transaction(self.database_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO patient_profiles(phone_number, name, age, gender, preferred_language)
                VALUES (?, ?, ?, ?, ?)
                """,
                (phone_number, name, age, gender, language),
            )
            return int(cursor.lastrowid)

    def update_profile(self, profile_id: int, preferred_language: str) -> None:
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                UPDATE patient_profiles
                SET preferred_language = ?
                WHERE id = ?
                """,
                (preferred_language, profile_id),
            )

    def get_profile(self, profile_id: int) -> dict[str, Any] | None:
        with transaction(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT id, phone_number, name, age, gender, preferred_language, created_at
                FROM patient_profiles
                WHERE id = ?
                """,
                (profile_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_or_create_conversation(self, phone_number: str, language: str) -> dict[str, Any]:
        with transaction(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM conversations
                WHERE phone_number = ? AND status IN ('active', 'queued_for_gp', 'referred')
                ORDER BY id DESC
                LIMIT 1
                """,
                (phone_number,),
            ).fetchone()
            if row:
                return dict(row)
            cursor = connection.execute(
                """
                INSERT INTO conversations(phone_number, stage, status, language, context_json)
                VALUES (?, 'start', 'active', ?, ?)
                """,
                (phone_number, language, dump_context({})),
            )
            created = connection.execute("SELECT * FROM conversations WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return dict(created)

    def update_conversation(
        self,
        conversation_id: int,
        *,
        patient_profile_id: int | None = None,
        stage: str | None = None,
        status: str | None = None,
        language: str | None = None,
        context: dict[str, Any] | None = None,
        active_case_id: int | None = None,
    ) -> None:
        updates: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
        values: list[Any] = []
        if patient_profile_id is not None:
            updates.append("patient_profile_id = ?")
            values.append(patient_profile_id)
        if stage is not None:
            updates.append("stage = ?")
            values.append(stage)
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if language is not None:
            updates.append("language = ?")
            values.append(language)
        if context is not None:
            updates.append("context_json = ?")
            values.append(dump_context(context))
        if active_case_id is not None:
            updates.append("active_case_id = ?")
            values.append(active_case_id)
        values.append(conversation_id)
        query = f"UPDATE conversations SET {', '.join(updates)} WHERE id = ?"
        with transaction(self.database_path) as connection:
            connection.execute(query, tuple(values))

    def add_message(
        self,
        conversation_id: int,
        sender_role: str,
        message_type: str,
        original_text: str | None,
        translated_text: str | None = None,
        audio_url: str | None = None,
    ) -> None:
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO messages(conversation_id, sender_role, message_type, original_text, translated_text, audio_url)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, sender_role, message_type, original_text, translated_text, audio_url),
            )

    def get_available_gp(self) -> dict[str, Any] | None:
        with transaction(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT doctors.id, doctors.name, doctors.role, doctors.specialty
                FROM doctors
                JOIN users ON users.doctor_id = doctors.id
                WHERE doctors.role = 'gp'
                  AND doctors.available = 1
                  AND users.is_active = 1
                ORDER BY doctors.id
                LIMIT 1
                """
            ).fetchone()
            return dict(row) if row else None

    def create_case(
        self,
        conversation_id: int,
        patient_profile_id: int,
        summary_english: str,
        urgency: str,
        specialty_hint: str,
        assigned_doctor_id: int | None,
    ) -> int:
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                UPDATE cases
                SET status = 'superseded',
                    updated_at = CURRENT_TIMESTAMP
                WHERE patient_profile_id = ?
                  AND status IN ('queued_for_gp', 'referred', 'doctor_review')
                """,
                (patient_profile_id,),
            )
            cursor = connection.execute(
                """
                INSERT INTO cases(
                    conversation_id,
                    patient_profile_id,
                    summary_english,
                    urgency,
                    specialty_hint,
                    status,
                    assigned_doctor_id
                )
                VALUES (?, ?, ?, ?, ?, 'queued_for_gp', ?)
                """,
                (
                    conversation_id,
                    patient_profile_id,
                    summary_english,
                    urgency,
                    specialty_hint,
                    assigned_doctor_id,
                ),
            )
            return int(cursor.lastrowid)

    def list_cases(self, role: str, doctor_id: int | None = None) -> list[dict[str, Any]]:
        status_filter = "queued_for_gp" if role == "gp" else "referred"
        doctor_field = "assigned_doctor_id" if role == "gp" else "assigned_specialist_id"
        extra_filter = f" AND cases.{doctor_field} = ?" if doctor_id is not None else ""
        params: tuple[Any, ...] = (status_filter,) if doctor_id is None else (status_filter, doctor_id)
        with transaction(self.database_path) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    cases.id,
                    cases.summary_english,
                    cases.urgency,
                    cases.specialty_hint,
                    cases.status,
                    cases.referral_note,
                    cases.created_at,
                    patient_profiles.name AS patient_name,
                    patient_profiles.age AS patient_age,
                    patient_profiles.gender AS patient_gender,
                    patient_profiles.preferred_language AS patient_language,
                    doctors.name AS doctor_name
                FROM cases
                JOIN patient_profiles ON patient_profiles.id = cases.patient_profile_id
                LEFT JOIN doctors ON doctors.id = cases.{doctor_field}
                WHERE cases.status = ?
                {extra_filter}
                ORDER BY
                    CASE cases.urgency
                        WHEN 'emergency' THEN 1
                        WHEN 'high' THEN 2
                        ELSE 3
                    END,
                    cases.created_at ASC
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_case(self, case_id: int) -> dict[str, Any] | None:
        with transaction(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT
                    cases.*,
                    patient_profiles.name AS patient_name,
                    patient_profiles.age AS patient_age,
                    patient_profiles.gender AS patient_gender,
                    patient_profiles.preferred_language AS patient_language,
                    patient_profiles.phone_number AS phone_number,
                    gp.name AS gp_name,
                    specialist.name AS specialist_name
                FROM cases
                JOIN patient_profiles ON patient_profiles.id = cases.patient_profile_id
                LEFT JOIN doctors AS gp ON gp.id = cases.assigned_doctor_id
                LEFT JOIN doctors AS specialist ON specialist.id = cases.assigned_specialist_id
                WHERE cases.id = ?
                """,
                (case_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_messages(self, conversation_id: int) -> list[dict[str, Any]]:
        with transaction(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT sender_role, message_type, original_text, translated_text, audio_url, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id
                """,
                (conversation_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_doctors(self, role: str = "specialist") -> list[dict[str, Any]]:
        with transaction(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT doctors.id, doctors.name, doctors.role, doctors.specialty, doctors.available
                FROM doctors
                JOIN users ON users.doctor_id = doctors.id
                WHERE doctors.role = ?
                  AND users.is_active = 1
                ORDER BY doctors.specialty, doctors.name
                """,
                (role,),
            ).fetchall()
            return [dict(row) for row in rows]

    def refer_case(self, case_id: int, specialist_id: int, referral_note: str) -> None:
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                UPDATE cases
                SET status = 'referred',
                    assigned_specialist_id = ?,
                    referral_note = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (specialist_id, referral_note, case_id),
            )
            conversation_id = connection.execute("SELECT conversation_id FROM cases WHERE id = ?", (case_id,)).fetchone()["conversation_id"]
            connection.execute(
                """
                UPDATE conversations
                SET status = 'referred', stage = 'doctor_review', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (conversation_id,),
            )

    def complete_case(self, case_id: int, doctor_id: int, english_reply: str, translated_reply: str, audio_url: str) -> dict[str, Any]:
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO doctor_responses(case_id, doctor_id, english_reply, translated_reply, audio_url)
                VALUES (?, ?, ?, ?, ?)
                """,
                (case_id, doctor_id, english_reply, translated_reply, audio_url),
            )
            connection.execute("UPDATE cases SET status = 'responded', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (case_id,))
            row = connection.execute("SELECT conversation_id, patient_profile_id FROM cases WHERE id = ?", (case_id,)).fetchone()
            connection.execute(
                """
                UPDATE conversations
                SET status = 'closed', stage = 'doctor_replied', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (row["conversation_id"],),
            )
            return {"conversation_id": row["conversation_id"], "patient_profile_id": row["patient_profile_id"]}

    def get_latest_response(self, case_id: int) -> dict[str, Any] | None:
        with transaction(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT doctor_responses.*, doctors.name AS doctor_name
                FROM doctor_responses
                JOIN doctors ON doctors.id = doctor_responses.doctor_id
                WHERE case_id = ?
                ORDER BY doctor_responses.id DESC
                LIMIT 1
                """,
                (case_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_conversation_context(self, conversation_id: int) -> dict[str, Any]:
        with transaction(self.database_path) as connection:
            row = connection.execute("SELECT context_json FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            return load_context(row["context_json"]) if row else load_context("{}")
